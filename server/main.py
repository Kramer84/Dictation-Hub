import os
import subprocess
import time
import json
import tempfile
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
CONFIG_JSON_PATH = os.path.join(REPO_ROOT, "configs", "pipeline_config.json")

# --- 1. Serve the Mobile UI ---
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(os.path.join(SERVER_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

# --- 2. The Shared Pipeline Execution Logic ---
# Both the desktop script (POST) and the mobile web app (WebSockets) use this exact engine.
def execute_pipeline(workspace, raw_audio, timestamp, profile_name, query_params):
    norm_wav = os.path.join(workspace, f"{timestamp}_raw.wav")
    json_out = os.path.join(workspace, f"{timestamp}_full.json")
    
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
    with os.fdopen(fd, 'w') as f:
        with open(os.path.join(REPO_ROOT, "configs", base_env), "r") as base:
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

    # (Preserved your custom COMPRESS_REPETITIONS check)
    use_confidence = "true" if env_overrides.get("MARK_CONFIDENCE", "false").lower() == "true" else "false"
    compress_reps = "true" if env_overrides.get("COMPRESS_REPETITIONS", "false").lower() == "true" else "false"
    
    cleaner_args = ["python3", os.path.join(REPO_ROOT, "post_processing", "deterministic_cleaner.py")]
    if use_confidence == "true":
        cleaner_args.append("--mark-confidence")
    if compress_reps == "true":
        cleaner_args.append("--compress-repetitions")
    cleaner_args.append(json_out)
    
    subprocess.run(cleaner_args, check=True)
    
    cleaned_json = json_out.replace(".json", "_cleaned.json")
    with open(cleaned_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    raw_text = " ".join([seg["text"] for seg in data.get("segments", [])]).strip()
    
    raw_txt_path = json_out.replace(".json", "_raw.txt")
    with open(raw_txt_path, "w", encoding="utf-8") as f:
        f.write(raw_text)

    # --- Dynamic Post-Processing Execution ---
    final_text = raw_text
    current_input = raw_txt_path
    post_steps = profile_data.get("post_processing", [])
    
    for i, step in enumerate(post_steps):
        if not isinstance(step, dict):
            continue
            
        step_type = step.get("type")
        step_out = json_out.replace(".json", f"_step{i+1}.txt")
        
        if step_type == "llm":
            cmd = [
                "python3", os.path.join(REPO_ROOT, "post_processing", "llm_step_runner.py"),
                "--input", current_input,
                "--output", step_out,
                "--provider", step.get("provider", "local"),
                "--model", step.get("model", "llama3"),
                "--endpoint", step.get("endpoint", "http://localhost:11434/v1/chat/completions"),
                "--language", detected_lang,
                "--prompt", step.get("prompt", "")
            ]
            response_schema = step.get("response_schema")
            if response_schema:
                cmd.extend(["--schema", json.dumps(response_schema)])
            elif step.get("enforce_json", False):
                cmd.append("--enforce-json")
                
        elif step_type == "deterministic":
            script_name = step.get("script")
            script_path = os.path.join(REPO_ROOT, "post_processing", script_name)
            cmd = ["python3", script_path, "--input", current_input, "--output", step_out]
            
            if "dictionary" in step:
                cmd.extend(["--dict", os.path.join(REPO_ROOT, step["dictionary"])])
            if step.get("language") == "{language}":
                cmd.extend(["--language", detected_lang])
            if "args" in step:
                cmd.extend(step["args"].split())
        else:
            continue

        subprocess.run(cmd, check=True)
        current_input = step_out
        
        with open(current_input, "r", encoding="utf-8") as f:
            final_text = f.read().strip()

    open(os.path.join(workspace, ".completed"), 'a').close()
    return {"raw_text": raw_text, "final_text": final_text}


# --- 3. The Desktop Client Endpoint (HTTP POST) ---
@app.post("/transcribe")
async def transcribe(request: Request):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    profile_name = dict(request.query_params).get("profile", "standard")
    workspace = os.path.expanduser(f"~/.whisper_transcriptions/{timestamp}_{profile_name}")
    os.makedirs(workspace, exist_ok=True)
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