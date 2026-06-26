# SpeechToTextTranscriptionTool

A frictionless, high-speed, GPU-accelerated speech-to-text pipeline. This project isolates audio capture, `whisper.cpp` execution, and Mistral LLM post-processing into a dedicated, modular repository.

## Architecture

* **`core/audio_capture.sh`**: Handles hardware audio capture via ALSA and standardizes the output to 16kHz mono WAV files (the strict format required by Whisper).
* **`core/whisper_transcribe.sh`**: The main execution router. It accepts an audio file and an environment profile, dynamically passing the correct parameters (VAD, thresholds, beam size) to `whisper-cli`.
* **`core/live_dictate.sh`**: A wrapper for `whisper-stream` allowing for continuous real-time dictation using sliding window VAD parameters.
* **`configs/`**: Contains `.env` files. Every behavior variant (Standard, Creative, Fast, Code-Switching) is explicitly defined here. No parameters are hardcoded in the bash logic.
* **`post_processing/`**: Contains the Python scripts utilizing LLMs to reconstruct and clean transcriptions without destroying Word Error Rate.
* **`setup/fetch_models.sh`**: Dependency manager to pull specific Whisper models and the Silero VAD `.bin` files based on a manifest.
* **`client/`**: Lightweight remote clients allowing secondary machines to dictate via a Tailscale HTTP endpoint.

## Usage

*Documentation pending full implementation of the router scripts.*

## Roadmap & Known Issues

### Known Bugs
- **Audio Capture Termination**: Pressing `Ctrl+C` during dictation in `audio_capture.sh` successfully kills the `arecord` process via a trap, but fails to unblock the `read -r` standard input prompt. A secondary `Enter` keystroke is currently required to fully exit the capture state.

### 1. Contextual Routing & Profiles
- **CLI Sub-commands**: Introduce arguments to the main `dictate` wrapper (e.g., `dictate notes`, `dictate code`) to route behavior.
- **Dynamic Prompt Injection**: Prepend profile-specific context (like expected tool names, acronyms, or formatting rules) into the `INITIAL_PROMPT` variable sent to Whisper.
- **Watcher Integration**: Modify `execution_router.sh` to append profile suffixes to the generated workspace folder names (e.g., `20260626_143100_notes`). This will allow external listeners/cronjobs to detect specific dictation types and trigger downstream events.

### 2. Audio Pre-Processing 
Implementing the hooks in `audio_capture.sh` to leverage `ffmpeg` filters before passing data to Whisper:
- **EBU R128 Normalization**: Equalize volume differences if recording hardware changes or the speaker moves away from the microphone.
- **Aggressive Silence Stripping**: Use `silenceremove` to cut dead air prior to inference (as an alternative or supplement to Silero VAD).
- **Frequency Filtering**: High-pass filters (e.g., >200Hz) to drop ambient room rumble and desk-bumping noises.

### 3. Remote & Mobile Execution
- **Mobile Client**: Adapt the existing `client/dictate_client.sh` Tailscale logic into an HTTP-capable mobile interface (e.g., iOS Shortcuts) to dictate from a phone.
- **Low-Latency Streaming**: Upgrade the Tailscale endpoint to support live chunked streaming directly into `whisper-stream`. This will replace the current full-file transfer methodology and avoid the latency penalty of reconstructing the entire audio file prior to inference.

### 4. Advanced Post-Processing
- **LLM Routing**: Integrate argument-specific LLM post-processing steps (e.g., summarizing, extracting action items) that trigger automatically after the deterministic cleaner completes and injects the raw output to the clipboard.

### 5. Testing and Benchmarking
- **GPU Benchmarking**: Wrapper around `whisper-bench` to stress-test inference speeds specifically on the GPU (`-ngl 99`) to identify the optimal thread-to-performance ratio for this specific hardware.
- **Word Error Rate (WER) Tool**: A script to evaluate raw Whisper outputs against ground-truth texts across multiple languages to quantitatively compare `.env` profiles.
- **Latent Space Evaluation (BERTScore)**: A future enhancement over standard WER. Standard WER penalizes the LLM for paraphrasing correctly (e.g., removing a stutter). Using a BERT encoder to compare semantic similarity will yield a much more accurate evaluation of the final post-processed text.