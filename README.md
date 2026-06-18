# SpeechToTextTranscriptionTool

A frictionless, high-speed, GPU-accelerated speech-to-text pipeline. This project isolates audio capture, `whisper.cpp` execution, and Mistral LLM post-processing into a dedicated, modular repository.

## Architecture

* **`core/audio_capture.sh`**: Handles hardware audio capture via ALSA and standardizes the output to 16kHz mono WAV files (the strict format required by Whisper).
* **`core/whisper_transcribe.sh`**: The main execution router. It accepts an audio file and an environment profile, dynamically passing the correct parameters (VAD, thresholds, beam size) to `whisper-cli`.
* **`core/live_dictate.sh`**: A wrapper for `whisper-stream` allowing for continuous real-time dictation using sliding window VAD parameters.
* **`configs/`**: Contains `.env` files. Every behavior variant (Standard, Creative, Fast, Code-Switching) is explicitly defined here. No parameters are hardcoded in the bash logic.
* **`post_processing/`**: Contains the Python scripts utilizing LLMs to reconstruct and clean transcriptions without destroying Word Error Rate.
* **`setup/fetch_models.sh`**: Dependency manager to pull specific Whisper models and the Silero VAD `.bin` files based on a manifest.

## Usage

*Documentation pending full implementation of the router scripts.*

## Future Works

### 1. Audio Pre-Processing 
Implementing the hooks in `audio_capture.sh` to leverage `ffmpeg` filters before passing data to Whisper:
- **EBU R128 Normalization**: Equalize volume differences if recording hardware changes or the speaker moves away from the microphone.
- **Aggressive Silence Stripping**: Use `silenceremove` to cut dead air prior to inference (as an alternative or supplement to Silero VAD).
- **Frequency Filtering**: High-pass filters (e.g., >200Hz) to drop ambient room rumble and desk-bumping noises.

### 2. Testing and Benchmarking
- **GPU Benchmarking**: Wrapper around `whisper-bench` to stress-test inference speeds specifically on the GPU (`-ngl 99`) to identify the optimal thread-to-performance ratio for this specific hardware.
- **Word Error Rate (WER) Tool**: A script to evaluate raw Whisper outputs against ground-truth texts across multiple languages to quantitatively compare `.env` profiles.
- **Latent Space Evaluation (BERTScore)**: A future enhancement over standard WER. Standard WER penalizes the LLM for paraphrasing correctly (e.g., removing a stutter). Using a BERT encoder to compare semantic similarity will yield a much more accurate evaluation of the final post-processed text.