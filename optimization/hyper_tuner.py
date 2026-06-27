#!/usr/bin/env python3
import os
import logging
import json
import optuna
from core.whisper_interface import run_whisper_trial
from core.evaluator import calculate_wer, normalize_text

DATASET_DIR = "optimization/dataset"
LOG_FILE = "optimization/hyper_tuning.log"

def get_test_cases(filter_noise=None):
    cases = []
    for folder in os.listdir(DATASET_DIR):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path): continue
        
        ref_path = os.path.join(folder_path, "reference.txt")
        meta_path = os.path.join(folder_path, "metadata.json")
        
        if not os.path.exists(ref_path) or not os.path.exists(meta_path): 
            continue
            
        with open(ref_path, "r", encoding="utf-8") as f:
            reference_text = f.read()
            
        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
            
        for file in os.listdir(folder_path):
            if file.endswith(".wav"):
                take_key = file.replace(".wav", "")
                take_meta = metadata.get(take_key, {})
                
                # Filter logic for sequential tuning
                is_noisy = take_meta.get("background_noise") in ['yes', 'y']
                if filter_noise is True and not is_noisy:
                    continue
                if filter_noise is False and is_noisy:
                    continue
                    
                cases.append({
                    "audio": os.path.join(folder_path, file),
                    "reference": reference_text,
                    "language": take_meta.get("language", "en")
                })
    return cases

# ==========================================
# STAGE 1: VAD OPTIMIZATION
# ==========================================
def objective_stage_1(trial):
    params = {
        "VAD_THOLD": trial.suggest_float("VAD_THOLD", 0.3, 0.9),
        "USE_VAD": "true",
        # Keep Whisper defaults static during VAD tuning
        "BEAM_SIZE": 5,
        "ENTROPY_THOLD": 2.4,
        "LOGPROB_THOLD": -1.0
    }
    
    # Only test on noisy audio to tune the pre-filter
    test_cases = get_test_cases(filter_noise=True)
    if not test_cases:
        raise ValueError("No noisy test cases found for Stage 1. Record noisy data first.")
        
    total_wer = 0
    for step, case in enumerate(test_cases):
        params["LANGUAGE"] = case["language"]
        output = run_whisper_trial(case["audio"], params)
        if not output: return float('inf')
            
        hypothesis = " ".join([seg.get('text', '') for seg in output.get('transcription', [])])
        total_wer += calculate_wer(normalize_text(case["reference"]), normalize_text(hypothesis))
        
        trial.report(total_wer / (step + 1), step)
        if trial.should_prune(): raise optuna.exceptions.TrialPruned()
            
    return total_wer / len(test_cases)

# ==========================================
# STAGE 2: DECODER OPTIMIZATION
# ==========================================
def objective_stage_2(trial, best_vad_params):
    params = {
        "BEAM_SIZE": trial.suggest_int("BEAM_SIZE", 2, 8),
        "ENTROPY_THOLD": trial.suggest_float("ENTROPY_THOLD", 1.8, 3.0),
        "LOGPROB_THOLD": trial.suggest_float("LOGPROB_THOLD", -2.0, -0.5),
        "USE_VAD": "true",
        "VAD_THOLD": best_vad_params["VAD_THOLD"]
    }
    
    # Tune decoder heuristics on clean audio to maximize wording accuracy
    test_cases = get_test_cases(filter_noise=False)
    if not test_cases:
        raise ValueError("No clean test cases found for Stage 2.")
        
    total_wer = 0
    for step, case in enumerate(test_cases):
        params["LANGUAGE"] = case["language"]
        output = run_whisper_trial(case["audio"], params)
        if not output: return float('inf')
            
        hypothesis = " ".join([seg.get('text', '') for seg in output.get('transcription', [])])
        total_wer += calculate_wer(normalize_text(case["reference"]), normalize_text(hypothesis))
        
        trial.report(total_wer / (step + 1), step)
        if trial.should_prune(): raise optuna.exceptions.TrialPruned()
            
    return total_wer / len(test_cases)

if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.FileHandler(LOG_FILE))
    optuna.logging.enable_propagation()  
    optuna.logging.disable_default_handler() 
    
    print("--- STARTING STAGE 1: VAD OPTIMIZATION ---")
    study_stage_1 = optuna.create_study(direction="minimize", pruner=optuna.pruners.MedianPruner(n_warmup_steps=2))
    study_stage_1.optimize(objective_stage_1, n_trials=20)
    best_vad = study_stage_1.best_params
    print(f"Stage 1 Complete. Optimal VAD: {best_vad['VAD_THOLD']:.2f}")
    
    print("\n--- STARTING STAGE 2: DECODER OPTIMIZATION ---")
    study_stage_2 = optuna.create_study(direction="minimize", pruner=optuna.pruners.MedianPruner(n_warmup_steps=2))
    study_stage_2.optimize(lambda trial: objective_stage_2(trial, best_vad), n_trials=30)
    
    print("\n========================================================")
    print(" Sequential Optimization Complete")
    print("========================================================")
    print(f"Final Average WER (Clean Data): {study_stage_2.best_value:.2%}")
    print("\nOptimal Parameters:")
    print(f'VAD_THOLD="{best_vad["VAD_THOLD"]:.2f}"')
    for key, value in study_stage_2.best_params.items():
        if isinstance(value, float):
            print(f'{key}="{value:.2f}"')
        else:
            print(f'{key}={value}')