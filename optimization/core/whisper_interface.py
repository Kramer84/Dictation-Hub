#!/usr/bin/env python3
import subprocess
import json
import os
import tempfile

def run_whisper_trial(audio_path, params):
    """
    Writes a temporary .env config inheriting standard.env, 
    applies Optuna overrides, and runs whisper_transcribe.sh.
    """
    params["OUTPUT_JSON_FULL"] = "true"
    params["OUTPUT_JSON"] = "false"
    params["OUTPUT_TXT"] = "false"
    params["OUTPUT_MD"] = "false"
    params["NO_PRINTS"] = "true"
    
    fd, temp_env_path = tempfile.mkstemp(suffix=".env", prefix="trial_")
    
    # Read the base configuration
    with open("configs/standard.env", "r") as base_env:
        base_config = base_env.read()
        
    with os.fdopen(fd, 'w') as f:
        # Inject base hardware and model settings
        f.write(base_config)
        f.write("\n# --- OPTUNA OVERRIDES ---\n")
        
        # Inject Optuna trial parameters
        for k, v in params.items():
            if isinstance(v, bool):
                v_str = "true" if v else "false"
            else:
                v_str = str(v)
            f.write(f'{k}="{v_str}"\n')
            
    output_base = os.path.splitext(audio_path)[0] + "_trial"
    expected_json = f"{output_base}.json"
    
    if os.path.exists(expected_json):
        os.remove(expected_json)
        
    cmd = [
        "bash", "core/whisper_transcribe.sh",
        "--input", audio_path,
        "--config", temp_env_path,
        "--output", output_base
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print("Whisper execution failed:")
            print(result.stderr)
            return None
            
        if not os.path.exists(expected_json):
            print(f"Expected JSON not generated: {expected_json}")
            return None
            
        with open(expected_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        return data
        
    finally:
        os.remove(temp_env_path)
        if os.path.exists(expected_json):
            os.remove(expected_json)

if __name__ == "__main__":
    # Built-in diagnostic test
    test_audio = "optimization/dataset/01_mesa_boogie_manual_extract/take_01.wav"
    
    if not os.path.exists(test_audio):
        print(f"Run the recording script first. Test audio missing: {test_audio}")
    else:
        test_params = {
            "BEAM_SIZE": 5,
            "ENTROPY_THOLD": 2.40,
            "LOGPROB_THOLD": -1.00,
            "LANGUAGE": "auto"
        }
        print("Booting diagnostic Whisper trial...")
        output = run_whisper_trial(test_audio, test_params)
        
        if output:
            segments = output.get('transcription', output.get('segments', []))
            raw_text = "".join([seg.get('text', '') for seg in segments])
            print("\n✅ Trial successful. Raw Output String:")
            print(raw_text[:250] + "...")