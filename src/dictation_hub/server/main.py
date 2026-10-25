import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import ClientDisconnect

from dictation_hub.pipeline.core.static_config import WhisperPipelineConfig
from dictation_hub.core.config_manager import USER_CONFIG_DIR
from dictation_hub.core.chunker import is_streamable_wav
from dictation_hub.pipeline.engine import setup_logging
from dictation_hub.server.access import AllowedClientsOnly
from dictation_hub.core.session import (
    TranscriptionError,
    complete_session,
    process_capture,
    start_transcription,
)

# 1. Resolve core paths
SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parents[2]

# 2. Check for user configs, fallback to defaults
CONFIG_JSON_PATH = USER_CONFIG_DIR / "pipeline_config.json"
STATIC_JSON_PATH = USER_CONFIG_DIR / "static.json"

if not STATIC_JSON_PATH.exists():
    CONFIG_JSON_PATH = REPO_ROOT / "configs" / "pipeline_config.json"
    STATIC_JSON_PATH = REPO_ROOT / "configs" / "static.json"

# Load static config
static_config = WhisperPipelineConfig.load_from_file(STATIC_JSON_PATH)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The server's stdout is its log file (server.log, journald): keep the details.
    setup_logging(verbose=True)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(AllowedClientsOnly)


def load_pipeline_config() -> dict:
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_known_profile(profile_name: str) -> bool:
    return profile_name in load_pipeline_config().get("profiles", {})


def unknown_profile_message(profile_name: str) -> str:
    known = ", ".join(sorted(load_pipeline_config().get("profiles", {})))
    return f"Unknown profile '{profile_name}'. Known profiles: {known}"

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    index_path = SERVER_DIR / "templates" / "index.html"
    return index_path.read_text(encoding="utf-8")

def _overrides(query_params: dict) -> dict:
    return {k: v for k, v in query_params.items() if k not in ("profile", "recording_id") and v != "auto"}


# Recordings the client named (``recording_id``), so it can ask for the result
# again when the connection that carried the upload is lost. Uploads in
# progress live in memory; finished results are kept on disk.
RECORDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_in_progress: dict = {}  # recording id -> {"state": ..., "received": ..., "owner": ...}


def _result_path(recording_id: str) -> Path:
    return static_config.storage.base_dir / ".results" / f"{recording_id}.json"


def recording_status(recording_id: str) -> dict:
    """``done`` (with the result), ``processing``, ``receiving`` (with the
    bytes received so far) or ``unknown`` (the client must send it again)."""
    path = _result_path(recording_id)
    if path.exists():
        return {"state": "done", "result": json.loads(path.read_text(encoding="utf-8"))}
    entry = _in_progress.get(recording_id)
    if entry is None:
        return {"state": "unknown"}
    if entry["state"] == "receiving":
        return {"state": "receiving", "received": entry["received"]}
    return {"state": entry["state"]}


def _store_result(recording_id: str, result: dict) -> None:
    path = _result_path(recording_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(path)


def _response(run) -> dict:
    try:
        result = run()
    except TranscriptionError as e:
        return {"raw_text": "", "final_text": "", "error": str(e)}
    response = {"raw_text": result.raw_text, "final_text": result.final_text}
    if result.error:
        response["error"] = result.error
    return response


def execute_pipeline(workspace: Path, raw_audio: Path, timestamp: str, profile_name: str, query_params: dict):
    """Process a complete upload (formats that cannot be chunked while streaming)."""
    config = load_pipeline_config()
    return _response(lambda: process_capture(
        capture=raw_audio,
        workspace=workspace,
        timestamp=timestamp,
        profile=profile_name,
        pipeline_config=config,
        static_config=static_config,
        overrides=_overrides(query_params),
        valid_args=config.get("valid_arguments", []),
    ))


def finish_live(job, workspace: Path, timestamp: str, profile_name: str) -> dict:
    """Finish a recording that was chunked while it streamed in."""
    config = load_pipeline_config()
    return _response(lambda: complete_session(job, workspace, timestamp, profile_name, config, static_config))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/result/{recording_id}")
async def get_result(recording_id: str):
    if not RECORDING_ID.fullmatch(recording_id):
        return JSONResponse(status_code=400, content={"error": "Invalid recording_id."})
    status = recording_status(recording_id)
    return JSONResponse(status_code=404 if status["state"] == "unknown" else 200, content=status)


@app.post("/transcribe")
async def transcribe(request: Request):
    query_params = dict(request.query_params)
    profile_name = query_params.get("profile", "standard")
    if not is_known_profile(profile_name):
        return JSONResponse(status_code=400, content={"error": unknown_profile_message(profile_name)})
    recording_id = query_params.get("recording_id")
    if recording_id is not None and not RECORDING_ID.fullmatch(recording_id):
        return JSONResponse(status_code=400, content={"error": "Invalid recording_id."})

    entry = {"state": "receiving", "received": 0}
    if recording_id is not None:
        status = recording_status(recording_id)
        if status["state"] == "done":
            # Sent again although it was already processed: answer from the store.
            async for _ in request.stream():
                pass
            return status["result"]
        # A newer upload of the same recording replaces an older one that may hang.
        _in_progress[recording_id] = entry

    def release() -> None:
        if recording_id is not None and _in_progress.get(recording_id) is entry:
            del _in_progress[recording_id]

    timestamp = time.strftime(static_config.storage.folder_format)
    workspace = static_config.storage.base_dir / f"{timestamp}_{profile_name}"
    workspace.mkdir(parents=True, exist_ok=True)
    raw_audio = workspace / f"{timestamp}_client.wav"

    job, head = None, b""
    try:
        try:
            with open(raw_audio, "wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
                    f.flush()  # the chunk worker reads the file while it grows
                    entry["received"] += len(chunk)
                    if job is not None:
                        job(b"")
                    elif head is not None:
                        head += chunk
                        if len(head) >= 44:
                            if is_streamable_wav(head):
                                config = load_pipeline_config()
                                job = start_transcription(
                                    raw_audio, workspace, timestamp, profile_name, config,
                                    overrides=_overrides(query_params),
                                    valid_args=config.get("valid_arguments", []),
                                    live=True,
                                )
                            head = None
        except ClientDisconnect:
            # The client quit or cancelled mid-recording; it keeps its own copy.
            if job is not None:
                await run_in_threadpool(job.cancel)
            shutil.rmtree(workspace, ignore_errors=True)
            return JSONResponse(status_code=499, content={"error": "Upload aborted by client."})

        if raw_audio.stat().st_size < 100:
            if job is not None:
                await run_in_threadpool(job.cancel)
            return {"raw_text": "Error: Received empty audio stream.", "final_text": ""}

        entry["state"] = "processing"
        if job is not None:
            result = await run_in_threadpool(finish_live, job, workspace, timestamp, profile_name)
        else:
            result = await run_in_threadpool(
                execute_pipeline, workspace, raw_audio, timestamp, profile_name, query_params
            )
        if recording_id is not None and (result.get("final_text") or result.get("raw_text")):
            _store_result(recording_id, result)
        return result
    finally:
        release()


@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    await websocket.accept()
    try:
        config = await websocket.receive_json()
    except Exception:
        await websocket.close()
        return

    profile_name = config.get("profile", "standard")
    if not is_known_profile(profile_name):
        await websocket.send_json({"error": unknown_profile_message(profile_name)})
        await websocket.close()
        return
    query_params = {"language": config.get("language", "auto")}

    timestamp = time.strftime(static_config.storage.folder_format)
    workspace = static_config.storage.base_dir / f"{timestamp}_{profile_name}"
    workspace.mkdir(parents=True, exist_ok=True)
    raw_audio = workspace / f"{timestamp}_client.webm"

    with open(raw_audio, "wb") as f:
        try:
            while True:
                data = await websocket.receive()
                if "text" in data and data["text"] == "EOF":
                    break
                if "bytes" in data:
                    f.write(data["bytes"])
        except WebSocketDisconnect:
            pass

    if os.path.getsize(raw_audio) < 100:
        await websocket.send_json({"error": "Received empty audio stream."})
        await websocket.close()
        return

    try:
        result = await run_in_threadpool(
            execute_pipeline, workspace, raw_audio, timestamp, profile_name, query_params
        )
        await websocket.send_json(result)
    except Exception as e:
        await websocket.send_json({"error": str(e)})
    finally:
        await websocket.close()
