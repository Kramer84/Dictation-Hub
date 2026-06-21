#!/usr/bin/env python3
import os
import random
import subprocess

DATASET_DIR = "optimization/dataset"
TAKES = 3

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
            
        for i in range(1, TAKES + 1):
            out_wav = os.path.join(folder_path, f"take_{i:02d}.wav")
            # Only add to queue if it hasn't been recorded yet
            if not os.path.exists(out_wav):
                tasks.append((folder, out_wav, text))

    if not tasks:
        print("All takes have already been recorded.")
        return

    random.shuffle(tasks)

    for idx, (folder, out_wav, text) in enumerate(tasks):
        print(f"\n========================================================")
        print(f" Task [{idx+1}/{len(tasks)}] | Target: {folder}")
        print(f"========================================================")
        print(f"\n{text}\n")
        print(f"--------------------------------------------------------")
        
        subprocess.run([
            "bash", "core/audio_capture.sh", 
            "--output", out_wav, 
            "--normalize"
        ])

if __name__ == "__main__":
    main()