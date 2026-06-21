#!/usr/bin/env python3
import os
import optuna
from core.whisper_interface import run_whisper_trial
from core.evaluator import calculate_wer, normalize_text

DATASET_DIR = "optimization/dataset"

def get_test_cases():
    cases = []
    for folder in os.listdir(DATASET_DIR):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path): 
            continue
        
        ref_path = os.path.join(folder_path, "reference.txt")
        if not os.path.exists(ref_path): 
            continue
        
        with open(ref_path, "r", encoding="utf-8") as f:
            reference_text = f.read()
            
        for file in os.listdir(folder_path):
            if file.endswith(".wav"):
                cases.append({
                    "audio": os.path.join(folder_path, file),
                    "reference": reference_text
                })
    return cases

def objective(trial):
    # Define the bounds of the hyperparameter search space
    params = {
        "BEAM_SIZE": trial.suggest_int("BEAM_SIZE", 2, 8),
        "ENTROPY_THOLD": trial.suggest_float("ENTROPY_THOLD", 1.8, 2.8),
        "LOGPROB_THOLD": trial.suggest_float("LOGPROB_THOLD", -2.0, -0.5),
        "NO_SPEECH_THOLD": trial.suggest_float("NO_SPEECH_THOLD", 0.3, 0.8),
        "VAD_THOLD": trial.suggest_float("VAD_THOLD", 0.4, 0.8),
        "LANGUAGE": "auto"
    }
    
    test_cases = get_test_cases()
    if not test_cases:
        raise ValueError("No test cases found in dataset directory.")
        
    total_wer = 0
    
    for case in test_cases:
        output = run_whisper_trial(case["audio"], params)
        
        if not output:
            # Heavily penalize configurations that cause outright execution failure
            return float('inf')
            
        segments = output.get('transcription', output.get('segments', []))
        hypothesis = " ".join([seg.get('text', '') for seg in segments])
        
        norm_ref = normalize_text(case["reference"])
        norm_hyp = normalize_text(hypothesis)
        
        total_wer += calculate_wer(norm_ref, norm_hyp)
        
    avg_wer = total_wer / len(test_cases)
    return avg_wer

if __name__ == "__main__":
    print("Starting Hyperparameter Optimization...")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    study = optuna.create_study(direction="minimize")
    
    try:
        # Run for 50 trials. Press Ctrl+C to stop early; Optuna will still print the best result found so far.
        study.optimize(objective, n_trials=50, show_progress_bar=True)
    except KeyboardInterrupt:
        print("\nOptimization interrupted by user.")
    
    print("\n========================================================")
    print(" Optimization Complete")
    print("========================================================")
    print(f"Best Average WER: {study.best_value:.2%}")
    print("\nOptimal Parameters (Copy into standard.env):")
    for key, value in study.best_params.items():
        if isinstance(value, float):
            print(f'{key}="{value:.2f}"')
        else:
            print(f'{key}={value}')