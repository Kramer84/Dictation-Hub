"""server/main.py"""
import os
import sys
import subprocess
import time
import json
import tempfile

# --- 1. Fix system paths BEFORE importing custom repository modules ---
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- 2. Now custom imports from across the repository will resolve safely ---
from post_processing.core.static_config import WhisperPipelineConfig

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

CONFIG_JSON_PATH = os.path.join(REPO_ROOT, "configs", "pipeline_config.json")
## The following will have to be changed, once we repackage the post_processing directory into a proper Python package with __init__.py
STATIC_JSON_PATH = os.path.join(REPO_ROOT, "configs", "static.json")

# --- Inject post_processing into path to access our shared configuration models ---
sys.path.append(os.path.join(REPO_ROOT, "post_processing"))

# Load static config once at server startup to reduce I/O overhead on requests
static_config = WhisperPipelineConfig.load_from_file(STATIC_JSON_PATH)

# --- 1. Serve the Mobile UI ---
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(os.path.join(SERVER_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

# --- 2. The Shared Pipeline Execution Logic ---
# Both the desktop script (POST) and the mobile web app (WebSockets) use this exact engine.
def execute_pipeline(workspace, raw_audio, timestamp, profile_name, query_params):
    # Dynamically apply suffixes from static_config
    norm_wav = os.path.join(workspace, f"{timestamp}{static_config.suffixes.audio}")
    json_out = os.path.join(workspace, f"{timestamp}{static_config.suffixes.full_json}")
    
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    profile_data = config.get("profiles", {}).get(profile_name, config["profiles"]["standard"])
    base_env = profile_data.get("env", "standard.env")
    env_overrides = profile_data.get("env_overrides", {})
    valid_args = config.get("valid_arguments", [])

    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", raw_audio, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", norm_wav
    ], check=True)
    
    fd, temp_env_path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        with open(os.path.join(REPO_ROOT, "configs", base_env), "r", encoding='utf-8') as base:
            f.write(base.read())
        f.write("\n# --- DYNAMIC OVERRIDES ---\n")
        for key, val in env_overrides.items():
            f.write(f'{key.upper()}="{val}"\n')
        for key, val in query_params.items():
            if key in valid_args and val != "auto":
                f.write(f'{key.upper()}="{val}"\n')

    transcribe_script = os.path.join(REPO_ROOT, "core", "whisper_transcribe.sh")
    
    subprocess.run([
        "bash", transcribe_script,
        "--input", norm_wav,
        "--config", temp_env_path,
        "--output", json_out
    ], check=True)
    
    os.remove(temp_env_path)

    detected_lang = "auto"
    if os.path.exists(json_out):
        with open(json_out, "r", encoding="utf-8") as f:
            detected_lang = json.load(f).get("language", "auto")
            
    metadata = {
        "profile": profile_name,
        "language": detected_lang,
        "timestamp": timestamp
    }
    with open(os.path.join(workspace, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    engine_script = os.path.join(REPO_ROOT, "post_processing", "engine.py")
    subprocess.run([
        "python3", engine_script,
        "--profile", profile_name,
        "--input", json_out,
        "--workspace", workspace
    ], check=True)

    # Output Resolution dynamically pulled from static_config
    final_txt_path = os.path.join(workspace, f"{timestamp}{static_config.suffixes.final_text}")
    raw_txt_path = os.path.join(workspace, f"{timestamp}{static_config.suffixes.raw_text}")

    # Engine creates the raw text dump from the deterministic cleaner step internally,
    # but if you need to fetch it for the web UI return:
    raw_text = ""
    if os.path.exists(raw_txt_path):
        with open(raw_txt_path, "r", encoding="utf-8") as f:
            raw_text = f.read().strip()

    final_text = ""
    if os.path.exists(final_txt_path):
        with open(final_txt_path, "r", encoding="utf-8") as f:
            final_text = f.read().strip()

    # --- N8N SYNCHRONOUS DISPATCH ---
    dispatcher_script = os.path.join(REPO_ROOT, "server", "n8n_dispatcher.py")
    subprocess.run([
        "python3", dispatcher_script,
        "--workspace", workspace
    ], check=False) # check=False ensures pipeline doesn't crash if n8n is offline
    open(os.path.join(workspace, ".completed"), 'a', encoding='utf-8').close()
    return {"raw_text": raw_text, "final_text": final_text}

# --- 3. The Desktop Client Endpoint (HTTP POST) ---
@app.post("/transcribe")
async def transcribe(request: Request):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    profile_name = dict(request.query_params).get("profile", "standard")
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{timestamp}_{profile_name}")
    os.makedirs(workspace, exist_ok=True)

    # Note: _client.wav acts as a temp incoming stream dump before ffmpeg normalizes it to static_config.suffixes.audio
    raw_audio = os.path.join(workspace, f"{timestamp}_client.wav")
    
    with open(raw_audio, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            
    if os.path.getsize(raw_audio) < 100:
        return {"raw_text": "Error: Received empty audio stream.", "final_text": ""}
        
    return execute_pipeline(workspace, raw_audio, timestamp, profile_name, dict(request.query_params))


# --- 4. The Mobile Client Endpoint (WebSocket) ---
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
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{timestamp}_{profile_name}")
    os.makedirs(workspace, exist_ok=True)

    # Note: _client.webm acts as a temp incoming stream dump before ffmpeg normalizes it to static_config.suffixes.audio
    raw_audio = os.path.join(workspace, f"{timestamp}_client.webm")
    
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
        result = execute_pipeline(workspace, raw_audio, timestamp, profile_name, query_params)
        await websocket.send_json(result)
    except Exception as e:
        await websocket.send_json({"error": str(e)})
    finally:
        await websocket.close()