import os
import subprocess
import time
import json
import tempfile
from fastapi import FastAPI, Request

app = FastAPI()

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
CONFIG_JSON_PATH = os.path.join(REPO_ROOT, "configs", "pipeline_config.json")

@app.post("/transcribe")
async def transcribe(request: Request):
    query_params = dict(request.query_params)
    profile_name = query_params.get("profile", "standard")
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    workspace_name = f"{timestamp}_{profile_name}"
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{workspace_name}")
    os.makedirs(workspace, exist_ok=True)
    
    raw_audio = os.path.join(workspace, f"{workspace_name}_client.wav")
    norm_wav = os.path.join(workspace, f"{workspace_name}_raw.wav")
    json_out = os.path.join(workspace, f"{workspace_name}_full.json")
    
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    profile_data = config.get("profiles", {}).get(profile_name, config["profiles"]["standard"])
    base_env = profile_data.get("env", "standard.env")
    valid_args = config.get("valid_arguments", [])

    with open(raw_audio, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            
    if os.path.getsize(raw_audio) < 100:
        return {"raw_text": "Error: Received empty audio stream.", "final_text": ""}
            
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", raw_audio, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", norm_wav
    ], check=True)
    
    fd, temp_env_path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, 'w') as f:
        with open(os.path.join(REPO_ROOT, "configs", base_env), "r") as base:
            f.write(base.read())
        f.write("\n# --- DYNAMIC OVERRIDES ---\n")
        for key, val in query_params.items():
            if key in valid_args:
                f.write(f'{key.upper()}="{val}"\n')

    transcribe_script = os.path.join(REPO_ROOT, "core", "whisper_transcribe.sh")
    
    subprocess.run([
        "bash", transcribe_script,
        "--input", norm