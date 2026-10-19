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

A fourth, programmatic entry point serves other projects (e.g. a data-annotation tool that has already asked the user a question): `dictation-hub answer` and `dictation_hub.api.dictate()`. Transcription is always the server's `standard` profile; the caller's own Pydantic schema and prompt then run as one LLM call on the calling side (Mistral by default), returning one JSON document. See `skills/integrating-dictation-hub/` and `docs/milestones/M7-external-api.md`.

---

## 2. Engineering Milestones & Roadmap

Development is phased to ensure that architectural gaps and existing bugs are cataloged, analyzed, and agreed upon before major code modifications occur.

> **Status (2026-10-15):** M0, M0.5, M1 (reboot test of the boot service still pending), M1b and M7 are done and merged to `main`. M2, M3, M4, M5, M6 are not started. Next up and open items: `docs/milestones/README.md` → "Next up". Section 6 below tells you how to resume.
>
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
    *   Send `SIGINT` to the recording subprocess (`arecord` / `parec` / `ffmpeg`; see "Audio capture backends" in §6) so audio file containers are cleanly finalized before normalization.
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

### Milestone 7: External Integration API (done 2026-10-15)
*   `dictation_hub.api` (`extract`, `dictate`) and the `dictation-hub answer` subcommand: record (or take `-f`), transcribe with the server's `standard` profile, run one schema-enforced LLM step (Mistral-first defaults, retry with error feedback), print one JSON document. Exit codes 0 ok / 1 recording or transcription failed / 2 bad input / 3 cancelled or quit / 4 LLM step failed.
*   Details, decisions and tickets: `docs/milestones/M7-external-api.md`. Agent guide: `skills/integrating-dictation-hub/`.

---

## 3. Agent Skills

Only **`skills/integrating-dictation-hub`** lives in this repo (calling Dictation-Hub from other projects, building the LLM schema step with Mistral defaults, lessons from the scheduling tool). `.claude/skills` is a symlink to `skills/`.

The other skills this project relies on (`tdd`, `diagnosing-bugs`, `handoff`, `code-review`, `wayfinder`, `model-router`, `writing-for-agents`, `codebase-index-keeper`) are **untracked, machine-local symlinks** into `/home/kramer84/ai_skills/…`, created by the user's `skills sync --profile core-dev --target .claude/skills/`. Do not commit them (absolute paths). On a machine without them, `tdd` means: write the failing test at an agreed public seam first, then the minimum code; one slice at a time. The older embedded copies (`implement-spec`, `to-tickets`, `optimize-for-gpu`, `get-available-resources`, `setup-pre-commit`, `improve-codebase-architecture`) were removed in commit `d26dbb4` and can be read from git history (`git show f34cfbb:skills/<name>/SKILL.md`). M2 will want `optimize-for-gpu` and `get-available-resources`; M6 `setup-pre-commit`.

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
# Environment and tests (tests need no mic, GPU or LLM; ~100 s; 9 `live` tests are deselected)
uv sync
uv run pytest                      # or: .venv/bin/python -m pytest
uv run pytest -m live              # needs real hardware / Ollama / Mistral

# Shell completion
dictation-hub --install-completion bash  # or zsh

# Dictate (host: records, uses the local server if running, else in-process)
dictation-hub standard
dictation-hub standard -l fr --threads 8      # any whisper option; -v shows every detail
dictation-hub standard -f /path/to/recording.wav   # reprocess a file (--local skips the server)
dictation-hub browse                          # browse past transcriptions

# WSL2 / no ALSA card: works out of the box through parec; to force a source or tool:
DICTATION_CAPTURE_COMMAND='parec --format=s16le --rate=16000 --channels=1 --raw' dictation-hub standard

# Client machine: set DICTATION_ROLE="client" and SERVER_IP/SERVER_PORT in ~/.config/dictation_hub/config.env
dictation-hub standard

# For other projects: one JSON document on stdout (needs MISTRAL_API_KEY for the default LLM step)
dictation-hub answer --schema my_project.schemas:Annotation
dictation-hub answer --profile-file annotate.json -f clip.wav

# Server
dictation-hub server install-service          # systemd --user unit, starts at boot (lingering)
dictation-hub server start [--host H] [--port P] | stop | status | logs

# Direct pipeline execution (engine debugging)
uv run python -m dictation_hub.pipeline.engine --profile scheduling \
    --input /path/to/<timestamp>_full.json --workspace /path/to/workspace/
```

Not implemented yet: `--type-active` / `--paste` (M4), GPU pre-flight (M2), voice-prefix routing (M5).

---

## 6. Resuming Work

**Where things stand:** `main` on GitHub (`Kramer84/Dictation-Hub`) holds everything above. No feature branch is open. Read `docs/milestones/README.md` (status table, dated log, "Next up") before choosing work.

**Repo map** (`src/dictation_hub/`):
*   `cli.py` — Typer router: bare profile names go to the hidden `run` command; `answer`, `browse`, `server …`. `answer_cli.py` — the `answer` command.
*   `api.py` — `extract`, `dictate`, `DictationResult` (external API). 
*   `core/` — `recorder.py` (mic capture backend choice, keys, listeners), `chunker.py` (silence cuts, chunked transcription), `session.py` (one flow capture → transcript → pipeline → webhook; used by CLI and server), `local_runner.py` (host CLI flow, server-first routing), `postprocess.py` (degradation to scrubbed text), `invocation.py` (CLI parsing and completion), `whisper_interface.py`, `config_manager.py`, `console.py` (terminal output, clipboard). `record_audio.py` is legacy (still used by `optimization/` and `session.py`'s audio preparation).
*   `client/` — `remote.py` (client role and server upload), `uploader.py`. `server/` — `main.py` (FastAPI), `service.py` (systemd, bind), `access.py` (Tailscale/localhost filter), `n8n_dispatcher.py`.
*   `pipeline/` — `engine.py` (`PIPELINE_MAP`), `pipelines/` (one class per profile; `base.py` has `call_llm`), `core/` (`text_tools.py` scrubber and LanguageTool, `llm_providers.py` Mistral/Ollama).
*   Config: seeds in `configs/`, live copies in `~/.config/dictation_hub/` (edit those; they are not overwritten). Workspaces: `~/.whisper_transcriptions/<ts>_<profile>/`.

**Conventions that matter:**
*   TDD at agreed seams; fake the mic (`record_from_mic`), whisper (`session.whisper_transcribe`) and the LLM (`LocalProvider.generate` / `MistralProvider.generate`). Examples: `tests/test_api.py`, `tests/test_local_runner.py`. The user is not fluent in test jargon: say "a fake LLM answer" instead of "stub", and "the place a test calls the code from" instead of "seam".
*   Log every design decision and milestone step in `docs/milestones/README.md` (dated entry) and the milestone's own file.
*   Commits end with the Co-Authored-By line; push only when asked. Branch off `main` for new work.
*   `git checkout` onto a branch that still contains the old `skills/<name>/` folders collides with the untracked symlinks; use `git fetch . branch:main` to fast-forward without switching.

**LLM provider preference:** Mistral is preferred only for large loads and complex tasks; simple LLM steps should stay local (Ollama). `api.extract` *defaults* to Mistral on purpose (Ollama may be absent; do not switch it). A future Ollama-availability check may pick the default per machine.

**Audio capture backends (`core/recorder.py: capture_command()`):** the recorder needs a command that writes 16 kHz mono s16le PCM to stdout. It picks the first that applies: (1) `DICTATION_CAPTURE_COMMAND`, an explicit override (also used to replay a take in real time); (2) `arecord`, when `arecord -l` lists a sound card (native Linux); (3) `parec` (PulseAudio/PipeWire-pulse); (4) `ffmpeg` (`-f avfoundation` on macOS, `-f pulse` elsewhere). If none exists, recording fails with a message naming what to install. The refactor to one shared recorder (M1) had dropped the old client's backend probing (ffmpeg-pulse, arecord-pulse, SoX `rec`, `parecord`) and hardcoded `arecord`, which broke **WSL2/WSLg**: there ALSA has no sound card (`arecord: audio open error`) and the microphone exists only as a PulseAudio source (`RDPSource` via `PULSE_SERVER=unix:/mnt/wslg/PulseServer`). Fixed 2026-10-19. Diagnose a capture failure with `arecord -l`, `pactl list short sources`, and by running the command from `capture_command()` by hand; when a capture process dies, its stderr is now shown. SoX `rec` and `AUDIO_BACKEND` are not supported any more; use `DICTATION_CAPTURE_COMMAND`.

**Known gotchas:** on WSL2 the Windows microphone must be allowed for desktop apps (Windows Settings → Privacy → Microphone), or `parec` records silence; `pactl info` should show `Default Source: RDPSource`. The `uv tool` install of `dictation-hub` is editable from this checkout; a stale non-editable install ignores repo changes. French LanguageTool checks cost about 1.7 s every time. Chunk progress lines do not show when going through the server. When the server transcribes, the detected language does not come back (prompt says `auto`).

