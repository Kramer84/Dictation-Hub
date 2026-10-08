import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from dictation_hub.pipeline.core.static_config import WhisperPipelineConfig
from dictation_hub.core.config_manager import USER_CONFIG_DIR
from dictation_hub.core.session import TranscriptionError, process_capture

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

app = FastAPI()


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

def execute_pipeline(workspace: Path, raw_audio: Path, timestamp: str, profile_name: str, query_params: dict):
    config = load_pipeline_config()
    overrides = {k: v for k, v in query_params.items() if k != "profile" and v != "auto"}
    try:
        result = process_capture(
            capture=raw_audio,
            workspace=workspace,
            timestamp=timestamp,
            profile=profile_name,
            pipeline_config=config,
            static_config=static_config,
            overrides=overrides,
            valid_args=config.get("valid_arguments", []),
        )
    except TranscriptionError as e:
        return {"raw_text": "", "final_text": "", "error": str(e)}
    response = {"raw_text": result.raw_text, "final_text": result.final_text}
    if result.error:
        response["error"] = result.error
    return response


@app.post("/transcribe")
async def transcribe(request: Request):
    timestamp = time.strftime(static_config.storage.folder_format)
    profile_name = dict(request.query_params).get("profile", "standard")
    if not is_known_profile(profile_name):
        return JSONResponse(status_code=400, content={"error": unknown_profile_message(profile_name)})

    workspace = static_config.storage.base_dir / f"{timestamp}_{profile_name}"
    workspace.mkdir(parents=True, exist_ok=True)
    raw_audio = workspace / f"{timestamp}_client.wav"

    with open(raw_audio, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)

    if raw_audio.stat().st_size < 100:
        return {"raw_text": "Error: Received empty audio stream.", "final_text": ""}

    return await run_in_threadpool(
        execute_pipeline, workspace, raw_audio, timestamp, profile_name, dict(request.query_params)
    )


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
