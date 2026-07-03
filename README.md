# SpeechToTextTranscriptionTool

A modular, client-server speech-to-text pipeline designed to eliminate common transcription hallucinations, enforce domain-specific formatting, and act as a reliable input trigger for external automation tools. 

This project isolates audio capture, `whisper.cpp` execution, multi-stage post-processing, and remote inference into a dedicated repository.

## Motivation & Philosophy

Standard speech-to-text tools often struggle with consistent formatting, phonetic hallucinations of specific technical terms or names, and poor adaptation to different contexts (e.g., dictating an email vs. a technical CLI command). 

This tool was built to solve these issues by:
1. **Using Intent-Based Profiles:** Before dictation, a specific profile is selected. This profile dictates the `INITIAL_PROMPT` sent to Whisper and determines the exact sequence of post-processing steps.
2. **Multi-Stage Post Processing:** Outputs are passed through deterministic regex replacements, local grammatical truecasing (LanguageTool), and optional LLM agents to restructure the text or propose different approaches.
3. **Intentional Manual Control:** Profile selection is strictly manual. Using an LLM first-pass to guess the user's intent would introduce latency and unpredictability; explicit selection guarantees the correct pipeline is executed immediately.
4. **Whole-Audio Processing:** While streaming audio directly to the decoder might decrease turnaround time, whole-audio processing is currently used to guarantee maximum flexibility with Whisper's inference parameters. Streaming options require further testing regarding contextual accuracy and parameter constraints.

## Architecture

The pipeline is split into a GPU-accelerated server and lightweight clients connected via a Tailscale network bridge.

### 1. Server (Inference & Processing)
The host machine handles all heavy lifting. It runs a FastAPI backend bound to the Tailscale interface.
* **Audio Capture:** Hardware audio is captured, converted to 16kHz mono, and peak-normalized to -6dB to ensure the Voice Activity Detection (VAD) operates on consistent intensity levels.
* **Whisper Router:** The core execution router reads the requested profile, dynamically builds the `whisper.cpp` parameters, and generates a raw JSON transcription.
* **Post-Processing:** A configurable chain of Python scripts cleans the JSON.
    * **Deterministic Cleaner:** Strips filler words and collapses repeating hallucination loops.
    * **Regex Replacer:** Fixes known phonetic failures (e.g., specific names, software libraries).
    * **Grammar Checker:** Restores proper casing and punctuation using a local `LanguageTool` daemon.
    * **LLM Runner:** Optional integration with local models (Ollama) or external APIs (Mistral) for complex formatting or data extraction.

### 2. Client Interfaces
Allows low-power devices (laptops without GPUs, phones) to offload inference to the host.
* **Terminal CLI (`client/dictate_client.sh`):** A remote dictation tool using an Auto-Discovery hardware bridge (PulseAudio, SoX, ALSA). It streams chunked audio data over a named pipe (FIFO) directly to the server via an HTTP POST request.
* **Mobile Web UI:** The FastAPI server serves a responsive HTML/JS interface accessible via Tailscale, utilizing WebSockets to stream audio directly from a browser.

### 3. Automation Integration
All files and intermediate pipeline steps are saved to `~/.whisper_transcriptions/` (configurable). Once a pipeline finishes, an empty `.completed` file is touched. This serves as a trigger for external directory-watching agents (e.g., n8n, custom cron jobs) to pick up the final payload (like a calendar schedule extraction) and execute API calls.

## Optimization & Tuning Suite

The repository contains a standalone `optimization/` suite to find the mathematical best fit for your specific hardware and voice.
* **VAD Optimization:** A live testing tool to find the exact threshold where Silero VAD cuts dead air without clipping the start of words.
* **Hyper-Tuner:** An Optuna-based parameter search that runs Whisper against a custom dataset. The dataset contains multiple takes of ground-truth reference texts recorded under different conditions (clean, fan noise, filler words, pauses) to find the most resilient beam search and entropy thresholds.

## Usage

### Remote Dictation (Tailscale)
1. On the GPU host machine, start the background server:
   `bash server/launch_server.sh`
2. **Via CLI:** On the client machine, invoke the global symlink (`dictate`), then press `Enter` or `Ctrl+C` to stop recording.
3. **Via Web:** Navigate to the Tailscale IP of the host on port 8000, select your profile, and dictate.

## Roadmap

### 1. Orchestration Rewrite
* **Python Migration:** The core orchestration will be rewritten in Python to improve error handling and configuration parsing, moving away from brittle bash-based JSON processing.

### 2. Dataset Expansion
* Expand the optimization dataset with more varied environmental noise profiles and languages to further harden the Whisper hyperparameter defaults.

### 3. RAG Interfacing
* **Pipeline Triggers:** Create specific profiles that output structured JSON queries designed to trigger external Retrieval-Augmented Generation (RAG) databases (e.g., searching scientific papers). The RAG logic will remain entirely external; this tool will solely act as the highly accurate, formatted input mechanism.