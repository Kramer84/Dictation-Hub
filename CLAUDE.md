# CLAUDE.md — Dictation-Hub Architecture, Specification & Roadmap

## 1. System Vision & Architecture

**Dictation-Hub** is a modular speech-to-text dictation and post-processing orchestration engine. It converts spoken audio into structured outputs (clean text, formatted email drafts, calendar events, shell commands, or structured notes) using local Whisper transcription and LLM refinement.

The system exposes three distinct entry points running against a centralized GPU processing host:


```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                     ENTRY POINTS                                       │
├──────────────────────────┬─────────────────────────────┬───────────────────────────────┤
│ 1. Local Host CLI        │ 2. Remote Client CLI        │ 3. Web UI (Mobile / Browser) │
│ (Direct GPU execution,   │ (CLI on remote Linux node,  │ (FastAPI + WebSocket          │
│  hotkeys, active window  │  streams WAV over HTTP/WS   │  MediaRecorder stream via     │
│  text injection)         │  to host server:8000)       │  phone or desktop browser)    │
└────────────┬─────────────┴──────────────┬──────────────┴───────────────┬───────────────┘
│                            │                              │
▼                            ▼                              ▼
┌─────────────────────────┐  ┌───────────────────────────────────────────────────────────┐
│ Host Local Runner       │  │ Host FastAPI Server (server/main.py:8000)                 │
│ (core/local_runner.py)  │  │ - Receives chunked audio / WebSocket stream               │
└────────────┬────────────┘  │ - Spawns workspace and stages execution                   │
│               └─────────────────────────────┬─────────────────────────────┘
└──────────────────────────────┬──────────────┘
▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                  PROCESSING PIPELINE                                   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. Raw Audio Persistence & Normalization                                               │
│    - Raw audio saved immediately to workspace before any transform.                    │
│    - ffmpeg converts to 16kHz mono PCM with peak volume normalization.                │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. GPU Validation & Whisper.cpp Inference (core/whisper_interface.py)                  │
│    - Pre-flight check verifies active GPU/CUDA acceleration.                           │
│    - whisper-cli runs local ggml model; outputs full token/segment JSON.               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. Deterministic Scrubber (pipeline/core/text_tools.py)                                 │
│    - Hallucination, filler, and stutter deduplication.                                 │
│    - Repetition loop compression (e.g., [R3]).                                         │
│    - Regex dictionary replacement (hallucinations_dict.yaml).                         │
│    - LanguageTool truecasing and punctuation daemon (port 8081).                       │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. Domain Pipeline Execution (pipeline/pipelines/)                                    │
│    - Routing via explicit profile or dynamic voice prefix parsing.                     │
│    - Context injection (date, time, timezone, user profile metadata).                  │
│    - LLM refinement via local Ollama (JSON schema enforced) or Mistral API.            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 5. Output Delivery & Automation                                                        │
│    - System Clipboard: wl-copy (Wayland), xclip (X11), pbcopy (macOS).                 │
│    - Active Window Injection: Direct typing/pasting into focused desktop input field.  │
│    - Automation Webhooks: Payload dispatch to external targets (n8n, APIs).            │
└────────────────────────────────────────────────────────────────────────────────────────┘

```

---

## 2. Engineering Milestones & Roadmap

Development is phased to ensure that architectural gaps and existing bugs are cataloged, analyzed, and agreed upon before major code modifications occur.

> **Progress tracking:** status, decisions and the per-milestone log live in [`docs/milestones/`](docs/milestones/README.md). Update `docs/milestones/README.md` (status table + dated log entry) whenever a milestone advances or a design decision is made. The M0 audit (defect catalog D01–D31, architecture proposal, open decisions) is in [`docs/milestones/M0-audit.md`](docs/milestones/M0-audit.md).
>
> **Development style:** test-driven (`skills/tdd`). Write the failing test first, at an agreed seam. Tests must run without a mic, GPU or LLM. Mark live tests `@pytest.mark.live`.

### Milestone 0: Comprehensive Audit, Issue Cataloging & Architecture Roadmap
*   **Audit Scope**: Thoroughly inspect all subpackages:
    *   CLI argument handling & completion routing in `src/dictation_hub/cli.py`.
    *   Subprocess signals, audio capture, and normalization in `src/dictation_hub/core/record_audio.py`.
    *   Local pipeline orchestration and lifecycle in `src/dictation_hub/core/local_runner.py`.
    *   Remote streaming client in `src/dictation_hub/client/streamer.py`.
    *   Server intake endpoints and WebSocket handling in `src/dictation_hub/server/main.py`.
    *   Whisper argument builder and configuration overrides in `src/dictation_hub/core/whisper_interface.py`.
    *   Post-processing and LLM calling abstractions in `src/dictation_hub/pipeline/`.
*   **Deliverables**:
    1.  **Catalog of Defects & Structural Deficiencies**:
        *   Unclear local recording controls (Ctrl-C/Enter semantics are ad hoc; replace with explicit key commands).
        *   Rejection of standard flags (e.g., `-l fr`) due to strict Typer command routing.
        *   Shell completion restricted to profile names, ignoring flags and parameters.
        *   Silent CPU fallback on heavy models (`large-v3`) when NVIDIA driver drops out.
        *   Lack of offline audio reprocessing (`--file` / rerun mechanism).
        *   Absence of direct desktop text injection (typing/pasting into the active focused window).
    2.  **Architectural Proposal**:
        *   Formulate a modular structure separating audio capture, GPU validation, CLI parsing, pipeline execution, and desktop output.
        *   Define target Pydantic models, CLI contracts, and error recovery policies.
    3.  **Review & Consensus**:
        *   Present findings and options; confirm final design decisions before advancing to Milestone 1.

### Milestone 1: Audio Capture Resilience, Recording Controls & Offline Reprocessing
*   **Explicit Recording Controls**:
    *   Replace the Enter/Ctrl-C stop behavior with explicit key commands (e.g. `s` start/stop, `q` quit); exact keymap to be agreed during M0. Ctrl-C may simply quit.
    *   Send `SIGINT` to the recording subprocess (`arecord` / `ffmpeg`) so audio file containers are cleanly finalized before normalization.
*   **Audio Reprocessing Fallback (`--file` / `-f`)**:
    *   Add `--file /path/to/audio.wav` flag to bypass live recording.
    *   Enable rerunning transcription and downstream pipelines on existing raw files without re-recording.
*   **Raw Audio Safeguard**:
    *   Persist the untouched recording (`<ts>_raw.wav`) in the workspace. It must never be deleted or overwritten by a processed version.
*   **Chunked Capture & Incremental Transcription**:
    *   Write audio to disk in chunks *during* recording (cut at silences, roughly 15–30 s), and transcribe each closed chunk in the background, ideally on a resident `whisper-server`, while recording continues.
    *   On stop, only the last chunk remains to process. The per-chunk results are merged (with time offsets) into a single `<ts>_full.json`, so latency after stop stays the same however long the recording was.
    *   Whole-file peak normalization blocks this and is to be replaced (per-chunk gain or none, chosen by measuring WER).
    *   The same chunker serves live mic, network streams and `--file`.

### Milestone 2: GPU Safety Guards & Hardware Pre-Flight Checks
*   **Driver & Compute Validation**:
    *   Integrate pre-flight checks using `skills/get-available-resources` and `skills/optimize-for-gpu`.
    *   Probe GPU/CUDA availability (e.g., via `nvidia-smi` exit check or PyTorch CUDA probe).
*   **Fail-Fast Fallback**:
    *   Prevent inadvertent multi-minute CPU execution on `large-v3` models when GPU initialization fails.
    *   Prompt the user or halt cleanly with an actionable warning if GPU acceleration is missing.

### Milestone 3: CLI Router Redesign & Dynamic Shell Completion
*   **Unconstrained Option Pass-Through**:
    *   Enable `allow_extra_args=True` and `ignore_unknown_options=True` on the `run` Typer command.
    *   Support explicit flags (`-l`, `--language`, `--model`, `--threads`) and forward unlisted Whisper options into `ctx.args`.
*   **Context-Aware Shell Completion**:
    *   Extend `DictationRouter.shell_complete` to dynamically return available options (parsed from `configs/standard.env` and `pipeline_config.json`) when the user enters `-` or `--`.

### Milestone 4: Desktop Integration & Active Window Text Injection
*   **Active Window Output**:
    *   Add `--type-active` / `--paste` flags.
    *   Inspect display environment (`WAYLAND_DISPLAY` vs `DISPLAY`).
    *   Implement paste emulation via clipboard and keystrokes (`wtype -M ctrl -k v` or `xdotool key ctrl+v`) to inject transcription directly into the active cursor position.
*   **Shortcut / Hotkey Setup**:
    *   Document and script system-level hotkey bindings (e.g., via GNOME shortcuts or `sway`/`hyprland` binds) to trigger host recording and automatic window paste.

### Milestone 5: Smart Voice-Prefix Intent Routing
*   **Prefix Parser**:
    *   Inspect the initial segment of the scrubbed transcription for vocal trigger phrases (e.g., *"Memo..."*, *"Schedule..."*, *"Draft email..."*, *"Terminal command..."*).
    *   Dynamically route to the corresponding domain pipeline (`siyuan_memo`, `scheduling`, `mail_drafting`, `cli_coder`) when no explicit profile is passed.
    *   Strip detected vocal triggers from the payload before passing it to the target pipeline.

### Milestone 6: Hardening, Test Suite & Production Hygiene
*   **Test-Driven Harness (`skills/tdd`)**:
    *   Add tests for CLI option parsing, argument pass-through, Pydantic schema parsing, and audio reprocessing.
*   **Linting & Pre-Commit (`skills/setup-pre-commit`)**:
    *   Standardize code style, formatting, and type checks across the codebase.

---

## 3. Integrated Agent Skills

The following skills are embedded in `skills/` to guide implementation passes:

*   **`skills/diagnosing-bugs`**: Root cause analysis for subprocess signal handling, Click argument parser failures, and audio capture crashes.
*   **`skills/improve-codebase-architecture`**: Decoupling the monolithic `local_runner.py` and `cli.py` modules into focused components.
*   **`skills/tdd`**: Constructing unit and integration tests for argument parsing, schema validation, and pipeline transformations without requiring live audio input.
*   **`skills/implement-spec`**: Translating milestone definitions and requirements into concrete implementation passes.
*   **`skills/to-tickets`**: Decomposing milestone objectives into granular, trackable tasks.
*   **`skills/optimize-for-gpu`**: Configuring compute parameters, VRAM usage, and CUDA runtime flags for `whisper.cpp`.
*   **`skills/get-available-resources`**: Pre-flight inspection of GPU drivers, CUDA devices, and system memory.
*   **`skills/setup-pre-commit`**: Configuring repository hooks for automated formatting and linting.
*   **`skills/integrating-dictation-hub`**: Calling Dictation-Hub from other projects (`dictation-hub answer`, `dictation_hub.api`), building the LLM schema step with Mistral defaults, and lessons from the scheduling tool.
*   **`skills/handoff`**: Summarizing session progress, open architectural decisions, and current work state.

---

## 4. Technical Invariants & Execution Rules

> These are current defaults, not hard constraints. Propose better designs when you see them.

1.  **Audio File Safety First, Chunked**: Raw audio (mic or network) is written to disk progressively, in chunks, while recording, and the untouched full recording is kept. Processing works on chunks as they close rather than waiting for the whole file.
2.  **Explicit Recording Controls**: Recording is driven by explicit key commands (e.g. `s` start/stop, `q` quit), not by overloading Ctrl-C/Enter.
3.  **Deterministic Scrubber Priority**: Deterministic text cleaning (deduplication, repetition compression, regex correction, LanguageTool) must execute before sending input to an LLM.
4.  **Graceful Degradation**: If an LLM call fails or times out, the deterministic scrubbed text (`<timestamp>_raw.txt`) must be preserved and copied to the clipboard as a fallback.
5.  **Clipboard Universality**: Clipboard copy routines must probe Wayland (`wl-copy`), X11 (`xclip`), and macOS (`pbcopy`) sequentially before falling back gracefully.

---

## 5. Developer Cheatsheet & Common Commands

```bash
# Environment sync (using uv)
uv sync

# Reinstall shell completion
dictation-hub --install-completion bash  # or zsh

# Host Mode: Run standard dictation locally
dictation-hub standard

# Host Mode: Run with parameter overrides
dictation-hub standard -l fr --threads 8

# Host Mode: Reprocess an existing audio file
dictation-hub --file /path/to/recording.wav scheduling

# Host Mode: Dictate and paste directly into focused desktop window
dictation-hub standard --type-active

# Client Mode: Stream audio to host GPU server
dictation-hub standard --ip 192.168.1.50 --port 8000

# Server Management
dictation-hub server start [--host 0.0.0.0] [--port 8000]
dictation-hub server stop
dictation-hub server logs

# Direct Pipeline Execution (Engine Debugging)
uv run python -m dictation_hub.pipeline.engine \
    --profile scheduling \
    --input /path/to/<timestamp>_full.json \
    --workspace /path/to/workspace/

```
