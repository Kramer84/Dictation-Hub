# SpeechToTextTranscriptionTool & Dictation Hub

A modular, client-server speech-to-text pipeline designed to eliminate common transcription hallucinations, enforce domain-specific formatting, and act as a reliable input trigger for external automation tools.

This project was built with a **local-first philosophy**: the heavy lifting (inference, grammatical correction, regex replacements) is executed on your own hardware using `whisper.cpp` and local LLMs (via Ollama). External APIs like Mistral can be used for complex restructuring, but the tool is primarily designed to keep data local, fast, and private.

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
Audio is sent in chunks to the server to maximize speed and reduce latency.
* **Terminal CLI:** A remote dictation tool using an Auto-Discovery hardware bridge. It streams chunked audio data directly to the server via an HTTP POST request.
* **Mobile Web UI (HTML App):** The FastAPI server serves a responsive HTML/JS interface accessible via Tailscale from your phone. It utilizes WebSockets to stream chunked audio directly from your mobile browser.

## Intent-Based Profiles

Before dictation, a specific profile is selected. This profile dictates the `INITIAL_PROMPT` sent to Whisper and determines the exact sequence of post-processing steps. 

By default, the tool ships with several profiles (Standard, Technical, Mail Drafting, CLI Coder, Scheduling, etc.). **You are highly encouraged to prune and customize these profiles.** You can remove the ones you don't need or rewrite the prompts in the `pipeline_config.json` file to suit your exact workflow.

## Installation

### Prerequisites
1. **Tailscale:** Recommended for secure, seamless connection between your clients and your host server.
2. **whisper.cpp:** The core inference engine.

### Setup Steps
1. **Clone and install whisper.cpp:**
   ```bash
   bash setup/install_whisper.sh
   bash setup/fetch_models.sh

```

2. **Install the Dictation Hub package:**
Ensure you have Python 3.9+ installed. Run the following in the repository root:
```bash
pip install -e .

```


3. **Initialize Configuration:**
Running any command will auto-generate your configuration folder at `~/.config/dictation_hub/`. You can edit `config.env` to set your machine's role (Host or Client) and Tailscale IP.

## Usage

### 1. Launching the Server (Host Machine)

On your GPU host machine, start the FastAPI server. You can bind it directly to your Tailscale interface:

```bash
dictation-hub server start --host tailscale --port 8000

```

### 2. Dictating from a Client (Laptop/Desktop)

On a client machine connected via Tailscale, use the CLI to start streaming audio for a specific profile: The tool works in the same manner on the host as-well. 

```bash
dictation-hub technical

```

Press `Enter` or `Ctrl+C` to stop recording and receive the processed text in your clipboard.

### 3. Dictating from a Phone (Web UI)

Navigate to `http://<YOUR_TAILSCALE_IP>:8000` on your mobile browser. Select your profile from the dropdown, tap the microphone button to stream audio, and copy the formatted result directly to your phone's clipboard.

## Optimization & Tuning Suite

Because different microphones, rooms, and voices yield different results, this repository contains a standalone `optimization/` suite to find the mathematical best fit for your specific hardware.

* **VAD Optimization:** A live testing tool to find the exact threshold where Silero VAD cuts dead air without clipping the start of words.


* **Hyper-Tuner:** An Optuna-based parameter search that runs Whisper against a custom dataset to find the most resilient beam search and entropy thresholds.


See `optimization/README.md` for specific tuning instructions.
