import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from dictation_hub.pipeline.core.static_config import WhisperPipelineConfig
from dictation_hub.core.config_manager import USER_CONFIG_DIR, generate_temp_env
from dictation_hub.core.record_audio import get_max_volume, build_ffmpeg_filters, process_audio
from dictation_hub.core.whisper_interface import whisper_transcribe
from dictation_hub.core.postprocess import dispatch_webhook, run_post_processing
from dictation_hub.core.utils import save_metadata
from dictation_hub.pipeline.engine import PIPELINE_MAP, setup_logging

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
    norm_wav = workspace / f"{timestamp}{static_config.suffixes.audio}"
    json_out = workspace / f"{timestamp}{static_config.suffixes.full_json}"

    config = load_pipeline_config()
    profile_data = config["profiles"][profile_name]
    base_env = profile_data.get("env", "standard.env")
    env_overrides = profile_data.get("env_overrides", {})
    valid_args = config.get("valid_arguments", [])

    # --- 1. Audio Normalization ---
    max_vol = get_max_volume(raw_audio)
    target_peak = -6.0
    gain = (target_peak - max_vol) if max_vol is not None else None
    filters = build_ffmpeg_filters(normalize=True, remove_silence=False, highpass_filter=False, gain=gain)

    process_audio(
        input_path=raw_audio,
        output_path=norm_wav,
        sample_rate=16000,
        channels=1,
        codec="pcm_s16le",
        filters=filters
    )

    # --- 2. Environment Config Generation ---
    base_env_path = REPO_ROOT / "configs" / base_env
    cli_overrides = {
        k: v for k, v in query_params.items() if k != "profile" and v != "auto"
    }

    temp_env_path = generate_temp_env(
        base_env_path=base_env_path,
        env_overrides=env_overrides,
        cli_overrides=cli_overrides,
        valid_args=valid_args
    )

    # --- 3. Whisper Transcription ---
    try:
        whisper_transcribe(
            input_wav=norm_wav,
            config=temp_env_path,
            output_base=json_out.with_suffix("")
        )
    finally:
        temp_env_path.unlink(missing_ok=True)

    # --- 4. Metadata Extraction ---
    save_metadata(workspace, json_out, profile_name, timestamp)

    # --- 5. Post-Processing Pipeline Execution ---
    setup_logging(workspace)
    pipeline_class = PIPELINE_MAP.get(profile_name, PIPELINE_MAP["standard"])
    pipeline = pipeline_class(
        repo_root=REPO_ROOT,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=workspace,
        user_information=config.get("user_information", {}),
    )
    result = run_post_processing(
        pipeline, json_out, workspace, timestamp, static_config.suffixes
    )

    # --- 6. Webhook / Automation Trigger ---
    dispatch_webhook(profile_data, result, workspace)

    (workspace / ".completed").touch()

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
