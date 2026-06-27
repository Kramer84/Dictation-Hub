#!/usr/bin/env python3
import sys
import warnings
import numpy as np

try:
    import torch
    import sounddevice as sd
except ImportError:
    print("Error: Missing required libraries.")
    print("Run: pip install sounddevice numpy torch torchaudio")
    sys.exit(1)

# Suppress PyTorch Hub warnings for a cleaner CLI experience
warnings.filterwarnings("ignore")

def print_menu(thold, min_s, min_sil, has_audio):
    print("\n" + "="*55)
    print(" LIVE VAD OPTIMIZATION TOOL ")
    print("="*55)
    print(" Parameters:")
    print(f"   [V] VAD Threshold:       {thold:.2f} (0.0 to 1.0)")
    print(f"   [S] Min Speech (ms):     {min_s}")
    print(f"   [L] Min Silence (ms):    {min_sil}")
    print("-" * 55)
    print(" Audio Actions:")
    print("   [1] Record Live Audio Snippet")
    if has_audio:
        print("   [2] Play Original Recording")
        print("   [3] Test: Play SPEECH (What Whisper will hear)")
        print("   [4] Test: Play NOISE (What got cut out)")
    print("   [Q] Quit")
    print("="*55)

def main():
    print("Loading Silero VAD model (this may take a moment on first run)...")
    # trust_repo=True prevents interactive blocks on newer PyTorch versions
    model, utils = torch.hub.load(
        repo_or_dir='snakers4/silero-vad',
        model='silero_vad',
        force_reload=False,
        trust_repo=True 
    )
    (get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils

    SAMPLING_RATE = 16000
    
    # Defaults corresponding to standard baseline
    vad_thold = 0.5
    min_speech_ms = 250
    min_silence_ms = 100
    
    wav_tensor = None
    
    while True:
        print_menu(vad_thold, min_speech_ms, min_silence_ms, wav_tensor is not None)
        choice = input("Select an option: ").strip().upper()
        
        if choice == 'Q':
            print("Exiting tool. Transfer your best parameters to search_space.py.")
            break
            
        elif choice == 'V':
            val = input("Enter new VAD Threshold (0.0 - 1.0): ")
            try: vad_thold = max(0.0, min(1.0, float(val)))
            except ValueError: print("Invalid input.")
            
        elif choice == 'S':
            val = input("Enter new Min Speech duration (ms): ")
            try: min_speech_ms = int(val)
            except ValueError: print("Invalid input.")
            
        elif choice == 'L':
            val = input("Enter new Min Silence duration (ms): ")
            try: min_silence_ms = int(val)
            except ValueError: print("Invalid input.")
            
        elif choice == '1':
            dur_str = input("Enter recording duration in seconds [5]: ")
            duration = int(dur_str) if dur_str.isdigit() else 5
            
            print(f"\n[RECORDING] Speak now for {duration} seconds...")
            print("Tip: Simulate your fan, type on your keyboard, or use filler words.")
            
            # Record float32 natively for direct PyTorch conversion
            recorded_audio = sd.rec(int(duration * SAMPLING_RATE), samplerate=SAMPLING_RATE, channels=1, dtype='float32')
            sd.wait()
            
            print("[DONE] Recording complete.")
            # Convert to 1D torch tensor required by Silero
            wav_tensor = torch.from_numpy(recorded_audio).squeeze()
            
        elif choice == '2' and wav_tensor is not None:
            print("\nPlaying Original Audio...")
            sd.play(wav_tensor.numpy(), samplerate=SAMPLING_RATE)
            sd.wait()
            
        elif choice in ['3', '4'] and wav_tensor is not None:
            print("\nApplying Neural Filter...")
            timestamps = get_speech_timestamps(
                wav_tensor, model,
                sampling_rate=SAMPLING_RATE,
                threshold=vad_thold,
                min_speech_duration_ms=min_speech_ms,
                min_silence_duration_ms=min_silence_ms
            )
            
            if choice == '3':
                if not timestamps:
                    print("=> No speech detected! Your VAD Threshold might be too aggressive.")
                else:
                    speech_tensor = collect_chunks(timestamps, wav_tensor)
                    print("Playing isolated SPEECH...")
                    sd.play(speech_tensor.numpy(), samplerate=SAMPLING_RATE)
                    sd.wait()
                    
            elif choice == '4':
                # Invert timestamps to isolate what the VAD rejected
                silenced = []
                last_end = 0
                for t in timestamps:
                    start, end = t['start'], t['end']
                    if start > last_end:
                        silenced.append({'start': last_end, 'end': start})
                    last_end = end
                
                # Catch trailing noise at the end of the clip
                if last_end < len(wav_tensor):
                    silenced.append({'start': last_end, 'end': len(wav_tensor)})
                
                if not silenced:
                    print("=> Nothing was silenced! The entire clip was passed to the decoder.")
                else:
                    noise_tensor = collect_chunks(silenced, wav_tensor)
                    print("Playing isolated NOISE...")
                    sd.play(noise_tensor.numpy(), samplerate=SAMPLING_RATE)
                    sd.wait()

if __name__ == "__main__":
    main()