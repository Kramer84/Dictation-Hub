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
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{timestamp}")
    os.makedirs(workspace, exist_ok=True)
    
    raw_audio = os.path.join(workspace, f"{timestamp}_client.wav")
    norm_wav = os.path.join(workspace, f"{timestamp}_raw.wav")
    json_out = os.path.join(workspace, f"{timestamp}_full.json")
    
    # 1. Gather dynamic arguments
    query_params = dict(request.query_params)
    profile_name = query_params.get("profile", "standard")
    
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    profile_data = config.get("profiles", {}).get(profile_name, config["profiles"]["standard"])
    base_env = profile_data.get("env", "standard.env")
    valid_args = config.get("valid_arguments", [])

    # 2. Receive stream
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
    
    # 3. Build Temporary Override ENV
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
        "--input", norm_wav,
        "--config", temp_env_path,
        "--output", json_out
    ], check=True)
    
    os.remove(temp_env_path)
    
    # 4. Standard Base Cleanup
    cleaner_script = os.path.join(REPO_ROOT, "post_processing", "deterministic_cleaner.py")
    subprocess.run([
        "python3", cleaner_script, "--compress-repetitions", json_out
    ], check=True)
    
    cleaned_json = json_out.replace(".json", "_cleaned.json")
    with open(cleaned_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    raw_text = " ".join([seg["text"] for seg in data.get("segments", [])]).strip()
    
    # Write raw text to file for pipeline ingestion
    raw_txt_path = json_out.replace(".json", "_raw.txt")
    with open(raw_txt_path, "w", encoding="utf-8") as f:
        f.write(raw_text)

    # 5. Dynamic Post-Processing Execution
    final_text = raw_text
    current_input = raw_txt_path
    post_steps = profile_data.get("post_processing", [])
    
    for i, script_cmd in enumerate(post_steps):
        step_out = json_out.replace(".json", f"_step{i+1}.txt")
        cmd = script_cmd.replace("{repo_root}", REPO_ROOT)
        cmd = f"{cmd} --input '{current_input}' --output '{step_out}'"
        
        subprocess.run(cmd, shell=True, check=True)
        current_input = step_out
        
        with open(current_input, "r", encoding="utf-8") as f:
            final_text = f.read().strip()

    # 6. Flag Completion for external watchers
    open(os.path.join(workspace, ".completed"), 'a').close()

    return {"raw_text": raw_text, "final_text": final_text}