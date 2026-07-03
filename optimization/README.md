# Transcription Optimization Suite

This directory contains an isolated suite of tools designed to mathematically tune the `whisper.cpp` inference parameters and Voice Activity Detection (VAD) thresholds for specific hardware and acoustic environments.

Because these tools require heavy machine-learning evaluation libraries, this suite operates entirely independently from the main transcription pipeline to prevent deployment bloat.

## Components

### 1. VAD Optimizer (`optimize_vad.py`)
A live, interactive testing tool for the Silero VAD model. 
It allows you to record live audio snippets (including background noise, fan noise, or typing) and interactively adjust thresholds. The tool can play back either the isolated speech or the rejected noise, allowing you to find the exact threshold where dead air is cut without clipping the start of words.

### 2. Hyperparameter Tuner (`hyper_tuner.py` & `search_space.py`)
An Optuna-based parameter search that evaluates `whisper.cpp` inference parameters (Beam Size, Entropy Thresholds, Log-Prob Thresholds) against a custom audio dataset. 
It utilizes Word Error Rate (WER) evaluation (`jiwer`) with custom penalty scoring heavily weighted against insertions (hallucinations) to find the most resilient beam search configuration.

### 3. Dataset Recorder (`record_dataset.py`)
A CLI utility to rapidly build the testing dataset. It prompts for reading ground-truth texts (`reference.txt`) under various tracked conditions (clean, fan noise, filler words) and manages the JSON metadata necessary for the Hyperparameter Tuner.

## Setup

The optimization suite requires its own Python virtual environment due to heavy dependencies.

```bash
cd optimization
python3 -m venv opt_venv
source opt_venv/bin/activate
pip install optuna jiwer torch sounddevice numpy torchaudio
```

## Usage

**To tune your VAD settings:**

```bash
python3 optimize_vad.py

```

**To run the hyperparameter search:**

1. Ensure you have recorded enough audio takes via `python3 record_dataset.py`.
2. Run the tuner:

```bash
python3 hyper_tuner.py

```

Check `hyper_tuning.log` for the Optuna trial results and optimal parameter configurations, which can then be transferred to the main repository's `.env` files.
