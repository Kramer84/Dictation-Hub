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
from dictation_hub.core.config_manager import USER_CONFIG_DIR

# 1. Resolve core paths
SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parents[2]  # Navigate up: server -> dictation_hub -> src -> REPO_ROOT

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
    index_path = SERVER_DIR / "index.html"
    return index_path.read_text(encoding="utf-8")

def execute_pipeline(workspace: Path, raw_audio: Path, timestamp: str, profile_name: str, query_params: dict):
    # Utilize statically defined suffixes for file extensions
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
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i", str(raw_audio),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11", str(norm_wav),
        ],
        check=True,
    )
    fd, temp_env_path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        base_env_path = REPO_ROOT / "configs" / base_env
        if base_env_path.exists():
            f.write(base_env_path.read_text(encoding="utf-8"))
        f.write("\n# --- DYNAMIC OVERRIDES ---\n")
        for key, val in env_overrides.items():
            f.write(f'{key.upper()}="{val}"\n')
        for key, val in query_params.items():
            if key in valid_args and val != "auto":
                f.write(f'{key.upper()}="{val}"\n')
    transcribe_script = REPO_ROOT / "core" / "whisper_transcribe.sh"
    subprocess.run(
        [
            "bash", str(transcribe_script),
            "--input", str(norm_wav),
            "--config", str(temp_env_path),
            "--output", str(json_out),
        ],
        check=True,
    )
    os.remove(temp_env_path)
    detected_lang = "auto"
    if os.path.exists(json_out):
        with open(json_out, "r", encoding="utf-8") as f:
            detected_lang = json.load(f).get("language", "auto")
    metadata = {
        "profile": profile_name,
        "language": detected_lang,
        "timestamp": timestamp,
    }
    metadata_path = workspace / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    engine_script = REPO_ROOT / "post_processing" / "engine.py"
    subprocess.run(
        [
            sys.executable, "-m", "dictation_hub.pipeline.engine",
            "--profile", profile_name,
            "--input", str(json_out),
            "--workspace", str(workspace),
        ],
        check=True,
        cwd=str(REPO_ROOT)
    )

    final_txt_path = workspace / f"{timestamp}{static_config.suffixes.final_text}"
    raw_txt_path = workspace / f"{timestamp}{static_config.suffixes.raw_text}"

    raw_text = raw_txt_path.read_text(encoding="utf-8").strip() if raw_txt_path.exists() else ""
    final_text = final_txt_path.read_text(encoding="utf-8").strip() if final_txt_path.exists() else ""

    subprocess.run(
        [
            sys.executable, "-m", "dictation_hub.server.n8n_dispatcher",
            "--workspace", str(workspace)
        ],
        check=False,
        cwd=str(REPO_ROOT)
    )

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
