#!/usr/bin/env python3
import os
import logging
import json
import optuna
from core.whisper_interface import run_whisper_trial
from core.evaluator import normalize_text, get_error_metrics
from search_space import get_search_space

DATASET_DIR = "optimization/dataset"
LOG_FILE = "optimization/hyper_tuning.log"

def get_test_cases():
    cases = []
    for folder in os.listdir(DATASET_DIR):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path): 
            continue
        
        ref_path = os.path.join(folder_path, "reference.txt")
        meta_path = os.path.join(folder_path, "metadata.json")
        
        if not os.path.exists(ref_path) or not os.path.exists(meta_path): 
            continue
        
        with open(ref_path, "r", encoding="utf-8") as f:
            reference_text = f.read().strip()
            
        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
            
        for file in os.listdir(folder_path):
            if file.endswith(".wav"):
                take_key = file.replace(".wav", "")
                take_meta = metadata.get(take_key, {})
                
                cases.append({
                    "audio": os.path.join(folder_path, file),
                    "reference": reference_text,
                    "language": take_meta.get("language", "en") # Bypasses auto-detect
                })
    return cases

# ==========================================
# DECODER OBJECTIVE FUNCTION
# ==========================================
def objective(trial):
    params = get_search_space(trial)
    test_cases = get_test_cases()
    
    if not test_cases:
        raise ValueError("No test cases found in dataset directory.")
        
    total_weighted_wer = 0
    
    for step, case in enumerate(test_cases):
        # Dynamically inject the known language from metadata into the trial parameters
        lang = case.get("language", "en")
        params["LANGUAGE"] = lang
        
        output = run_whisper_trial(case["audio"], params)
        
        if not output:
            return float('inf')
            
        segments = output.get('transcription', output.get('segments', []))
        hypothesis = " ".join([seg.get('text', '') for seg in segments])
        
        norm_ref = normalize_text(case["reference"], lang=lang)
        norm_hyp = normalize_text(hypothesis, lang=lang)
        
        metrics = get_error_metrics(norm_ref, norm_hyp)
        
        # CUSTOM PENALTY LOGIC
        # Define how much you hate each type of error.
        # This makes the tuner "smarter" about what it avoids.
        penalty_score = (
            (metrics["insertions"] * 1.5) +  # Hallucinations are costly
            (metrics["deletions"] * 1.0) +   # Missed words are standard
            (metrics["substitutions"] * 1.2) # Wrong words are problematic
        )
        
        # Normalize the penalty by the length of the reference 
        # so it remains a percentage-like score
        ref_len = len(norm_ref.split())
        weighted_wer = penalty_score / ref_len if ref_len > 0 else float('inf')
        
        total_weighted_wer += weighted_wer
        
        # Report intermediate performance to Optuna
        avg_wer_so_far = total_weighted_wer / (step + 1)
        trial.report(avg_wer_so_far, step)
        
        # Prune the trial if the first few files yield terrible WER compared to previous trials
        if trial.should_prune():
            raise optuna.exceptions.TrialPruned()
            
    avg_wer = total_weighted_wer / len(test_cases)
    return avg_wer

if __name__ == "__main__":
    print("Starting Hyperparameter Optimization...")
    
    # ==========================================
    # FILE LOGGING CONFIGURATION
    # ==========================================
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    
    optuna.logging.enable_propagation()  
    optuna.logging.disable_default_handler() 
    
    # MedianPruner aborts unpromising parameter sets early based on reported steps
    study = optuna.create_study(
        direction="minimize", 
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=2)
    )
    
    try:
        study.optimize(objective, n_trials=50, show_progress_bar=True)
    except KeyboardInterrupt:
        print("\nOptimization interrupted by user.")
    
    print("\n========================================================")
    print(" Optimization Complete")
    print("========================================================")
    print(f"Best Average WWER: {study.best_value:.2%}")
    print("\nOptimal Parameters:")
    for key, value in study.best_params.items():
        if isinstance(value, float):
            print(f'{key}="{value:.2f}"')
        else:
            print(f'{key}={value}')