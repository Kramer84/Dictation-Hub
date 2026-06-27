#!/usr/bin/env python3
import os
import random
import subprocess
import json

DATASET_DIR = "optimization/dataset"
TAKES = 5

def get_or_create_metadata(folder_path):
    meta_path = os.path.join(folder_path, "metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_metadata(folder_path, metadata):
    meta_path = os.path.join(folder_path, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)

def main():
    tasks = []
    
    for folder in os.listdir(DATASET_DIR):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path): 
            continue
        
        ref_path = os.path.join(folder_path, "reference.txt")
        if not os.path.exists(ref_path): 
            continue
        
        with open(ref_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
            
        metadata = get_or_create_metadata(folder_path)
            
        for i in range(1, TAKES + 1):
            take_key = f"take_{i:02d}"
            out_wav = os.path.join(folder_path, f"{take_key}.wav")
            
            if not os.path.exists(out_wav):
                tasks.append((folder, folder_path, out_wav, text, take_key, metadata))

    if not tasks:
        print(f"All {TAKES} takes have already been recorded for all references. No new data required.")
        return

    random.shuffle(tasks)

    for idx, (folder, folder_path, out_wav, text, take_key, metadata) in enumerate(tasks):
        print(f"\n========================================================")
        print(f" Task [{idx+1}/{len(tasks)}] | Target: {folder} | {take_key}")
        print(f"========================================================")
        print(f"\n{text}\n")
        print(f"--------------------------------------------------------")
        
        # New language identifier requirement
        lang_iso = input("What is the language ISO code? (e.g., en, fr, de, hu): ").strip().lower()
        
        bg_noise = input("Is there background noise? (yes/no): ").strip().lower()
        noise_type = "none"
        if bg_noise in ['yes', 'y']:
            noise_type = input("Noise type? (fan/music/other): ").strip().lower()
            
        fillers = input("Will there be filler words? (yes/no): ").strip().lower()
        pauses = input("Will there be long pauses? (yes/no): ").strip().lower()
        
        subprocess.run([
            "bash", "core/audio_capture.sh", 
            "--output", out_wav, 
            "--normalize"
        ])
        
        metadata[take_key] = {
            "language": lang_iso,
            "background_noise": bg_noise,
            "noise_type": noise_type,
            "filler_words": fillers,
            "long_pauses": pauses,
            "environment": "clean" if (bg_noise != 'yes' and fillers != 'yes' and pauses != 'yes') else "noisy/spontaneous"
        }
        save_metadata(folder_path, metadata)

if __name__ == "__main__":
    main()