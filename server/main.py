import os
import subprocess
import time
import json
from fastapi import FastAPI, Request

app = FastAPI()

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)

@app.post("/transcribe")
async def transcribe(request: Request):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{timestamp}")
    os.makedirs(workspace, exist_ok=True)
    
    raw_audio = os.path.join(workspace, f"{timestamp}_client.wav")
    norm_wav = os.path.join(workspace, f"{timestamp}_raw.wav")
    json_out = os.path.join(workspace, f"{timestamp}_full.json")
    
    with open(raw_audio, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            
    # Guardrail against 0-byte files caused by immediate client failures
    if os.path.getsize(raw_audio) < 100:
        return {"text": "Error: Received empty audio stream. The capture hardware on the client failed to initialize."}
            
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", raw_audio, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", norm_wav
    ], check=True)
    
    transcribe_script = os.path.join(REPO_ROOT, "core", "whisper_transcribe.sh")
    config_env = os.path.join(REPO_ROOT, "configs", "standard.env")
    
    subprocess.run([
        "bash", transcribe_script,
        "--input", norm_wav,
        "--config", config_env,
        "--output", json_out
    ], check=True)
    
    cleaner_script = os.path.join(REPO_ROOT, "post_processing", "deterministic_cleaner.py")
    subprocess.run([
        "python3", cleaner_script, "--compress-repetitions", json_out
    ], check=True)
    
    cleaned_json = json_out.replace(".json", "_cleaned.json")
    with open(cleaned_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    final_text = " ".join([seg["text"] for seg in data.get("segments", [])]).strip()
    
    return {"text": final_text}