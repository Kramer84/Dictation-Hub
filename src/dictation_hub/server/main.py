import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from dictation_hub.pipeline.core.static_config import WhisperPipelineConfig
from dictation_hub.core.config_manager import USER_CONFIG_DIR, generate_temp_env
from dictation_hub.core.record_audio import get_max_volume, build_ffmpeg_filters, process_audio
from dictation_hub.core.whisper_interface import whisper_transcribe
from dictation_hub.pipeline.engine import PIPELINE_MAP, setup_logging
from dictation_hub.server.n8n_dispatcher import push_to_n8n

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

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    index_path = SERVER_DIR / "template" / "index.html"
    return index_path.read_text(encoding="utf-8")

def execute_pipeline(workspace: Path, raw_audio: Path, timestamp: str, profile_name: str, query_params: dict):
    norm_wav = workspace / f"{timestamp}{static_config.suffixes.audio}"
    json_out = workspace / f"{timestamp}{static_config.suffixes.full_json}"

    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    profile_data = config.get("profiles", {}).get(
        profile_name, config["profiles"]["standard"]
    )
    base_env = profile_data.get("env", "standard.env")
    env_overrides = profile_data.get("env_overrides", {})
    valid_args = config.get("valid_arguments", [])

    # --- 1. Audio Normalization ---
    max_vol = get_max_volume(raw_audio)
    target_peak = -6.0
    gain = (target_peak - max_vol) if max_vol is not None else None
    filters = build_ffmpeg_filters(normalize=True, remove_silence=False, highpass=False, gain=gain)

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
    cli_overrides = {k: v for k, v in query_params.items() if v != "auto"}

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
    detected_lang = "auto"
    if json_out.exists():
        with open(json_out, "r", encoding="utf-8") as f:
            detected_lang = json.load(f).get("language", "auto")

    metadata = {
        "profile": profile_name,
        "language": detected_lang,
        "timestamp": timestamp,
    }
    metadata_path = workspace / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # --- 5. Post-Processing Pipeline Execution ---
    setup_logging(workspace)
    user_info = config.get("user_information", {})
    pipeline_class = PIPELINE_MAP.get(profile_name, PIPELINE_MAP["standard"])

    pipeline = pipeline_class(
        repo_root=REPO_ROOT,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=workspace,
        user_information=user_info,
    )

    final_text = pipeline.execute(json_out)

    # Save outputs
    final_txt_path = workspace / f"{timestamp}{static_config.suffixes.final_text}"
    final_txt_path.write_text(final_text, encoding="utf-8")

    raw_txt_path = workspace / f"{timestamp}{static_config.suffixes.raw_text}"
    raw_text = raw_txt_path.read_text(encoding="utf-8").strip() if raw_txt_path.exists() else ""

    # --- 6. Webhook / Automation Trigger ---
    webhook_url = profile_data.get("webhook_url")
    if webhook_url:
        try:
            payload = json.loads(final_text)
        except json.JSONDecodeError:
            payload = {"text_content": final_text}
        push_to_n8n(webhook_url, payload, workspace.name)

    (workspace / ".completed").touch()

    return {"raw_text": raw_text, "final_text": final_text}


@app.post("/transcribe")
async def transcribe(request: Request):
    timestamp = time.strftime(static_config.storage.folder_format)
    profile_name = dict(request.query_params).get("profile", "standard")

    workspace = static_config.storage.base_dir / f"{timestamp}_{profile_name}"
    workspace.mkdir(parents=True, exist_ok=True)
    raw_audio = workspace / f"{timestamp}_client.wav"

    with open(raw_audio, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)

    if raw_audio.stat().st_size < 100:
        return {"raw_text": "Error: Received empty audio stream.", "final_text": ""}

    return execute_pipeline(
        workspace, raw_audio, timestamp, profile_name, dict(request.query_params)
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
        result = execute_pipeline(
            workspace, raw_audio, timestamp, profile_name, query_params
        )
        await websocket.send_json(result)
    except Exception as e:
        await websocket.send_json({"error": str(e)})
    finally:
        await websocket.close()
