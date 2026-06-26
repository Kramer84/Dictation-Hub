# SpeechToTextTranscriptionTool

A frictionless, high-speed, GPU-accelerated speech-to-text pipeline. This project isolates audio capture, `whisper.cpp` execution, and Mistral LLM post-processing into a dedicated, modular repository.

## Architecture

* **`core/audio_capture.sh`**: Handles hardware audio capture and standardizes the output to 16kHz mono WAV files.
* **`core/whisper_transcribe.sh`**: The main execution router for local inference. It accepts an audio file and an environment profile, dynamically passing the correct parameters to `whisper-cli`.
* **`server/main.py`**: A FastAPI backend bound to Tailscale, exposing a `/transcribe` endpoint. It intercepts chunked transfer streams, processes the raw audio, and returns cleaned JSON payloads.
* **`client/dictate_client.sh`**: A remote dictation tool using an Auto-Discovery hardware bridge (PulseAudio, SoX, ALSA). It streams chunked data over a named pipe (FIFO) directly to the server, resulting in zero transfer latency.
* **`configs/`**: Contains `.env` files defining explicit behavioral variants (Standard, Creative, Fast, Code-Switching). 
* **`post_processing/`**: Python scripts utilizing LLMs to reconstruct and clean transcriptions deterministically.
* **`setup/`**: Dependency manager to pull specific Whisper models and the Silero VAD `.bin` files based on a manifest.

## Usage

### Local Dictation
Run the core execution router directly on the host machine.
*(Documentation pending parameter implementation)*

### Remote Dictation (Tailscale)
1. On the host machine, start the background server:
   `bash server/launch_server.sh`
2. On the client machine, invoke the global symlink:
   `dictate`
3. Press `Enter` or `Ctrl+C` to stop recording and fetch the transcribed text.

## Roadmap

### 1. Contextual Routing & Profiles
- **CLI Sub-commands**: Introduce arguments to the main `dictate` wrapper (e.g., `dictate notes`, `dictate code`) to route behavior.
- **Dynamic Prompt Injection**: Prepend profile-specific context (like expected tool names, acronyms, or formatting rules) into the `INITIAL_PROMPT` variable sent to Whisper.
- **Backend Query Integration**: Update the FastAPI `/transcribe` endpoint to accept query parameters so remote clients can trigger specific `.env` profiles.

### 2. Audio Pre-Processing 
- **EBU R128 Normalization**: Equalize volume differences if recording hardware changes or the speaker moves away from the microphone.
- **Aggressive Silence Stripping**: Use `silenceremove` to cut dead air prior to inference.

### 3. Advanced Post-Processing
- **LLM Routing**: Integrate argument-specific LLM post-processing steps (e.g., summarizing, extracting action items) that trigger automatically based on the requested profile.

### 4. Testing and Benchmarking
- **Word Error Rate (WER) Tool**: A script to evaluate raw Whisper outputs against ground-truth texts across multiple languages.
- **Latent Space Evaluation (BERTScore)**: A semantic similarity evaluator for post-processed text.