# Milestones — Status & Progress Log

The roadmap lives in `CLAUDE.md` §2. This folder tracks **progress**: what was done and what was decided, with one file per milestone.

| ID | Title | Status | Doc |
|----|-------|--------|-----|
| M0 | Audit, defect catalog, architecture proposal | 🟢 Done | [M0-audit.md](M0-audit.md) |
| M0.5 | Stabilize: repair tests, fix high-severity defects test-first | 🟢 Done (merged to main) | [M0.5-stabilize.md](M0.5-stabilize.md) |
| M1 | Session orchestrator, recording controls, 16 kHz capture, `--file`, server autostart/fallback | 🟢 Done (merged to main; boot-time reboot test pending) | [M1-session.md](M1-session.md) |
| M1b | Chunked capture & incremental transcription (VRAM-frugal) | 🟢 Done | [M1b-chunking.md](M1b-chunking.md) |
| M2 | GPU pre-flight & fail-fast | ⚪ Not started | — |
| M3 | CLI router redesign & completion | ⚪ Not started | — |
| M4 | Desktop active-window injection | ⚪ Not started | — |
| M5 | Voice-prefix intent routing | ⚪ Not started | — |
| M6 | Hardening, tests, pre-commit | ⚪ Not started (tests are written alongside every milestone, TDD) | — |
| M7 | External integration API: `dictate()` + `answer`, schema profiles as data | 🟢 Done | [M7-external-api.md](M7-external-api.md) |

Legend: ⚪ not started · 🟡 in progress / blocked on decision · 🟢 done

## Next up (as of 2026-10-15)

Everything is merged to `main`; no branch is open. Suggested order, with the user's priorities first:

1. **Real-world check of M7.** Run `dictation-hub answer --schema <module:Class>` once with a real `MISTRAL_API_KEY` and microphone (all M7 tests use fakes). Confirm that `mistral-small-latest` works on the account and that stdout is clean JSON while the recorder display goes to stderr. Then wire the annotation tool to it.
2. **Reboot test** of the systemd service (M1/S6): the server must answer before anyone logs in.
3. **M2 GPU pre-flight** (D13): `whisper-cli` silently falls back to CPU when CUDA init fails. Use `nvidia-smi` exit status and the whisper backend line; fail fast on large models.
4. **M3 CLI/completion** leftovers (D05: client flags). **M4** `--type-active`/`--paste`. **M5** voice-prefix routing. **M6** pre-commit and lint (also D31: dependency hygiene).

Small open items:
*   **Provider default (decided 2026-10-15): stays Mistral.** The user prefers Mistral only for large loads and complex tasks, but Ollama is not guaranteed to be present, so `api.extract` keeps Mistral as the default (`answer --schema …` needs no profile). Later idea: probe for Ollama (e.g. a quick request to `http://localhost:11434`) and choose the default provider from what is available, with simple schemas preferring local when it is.
*   The server does not return the detected language, so `answer` run through the server prompts with `auto`. Add `language` to the `/transcribe` response.
*   Two "now"s: the LLM prompt's context uses today's date, the scheduling pipeline resolves relative dates against the recording time (see `skills/integrating-dictation-hub/lessons-from-scheduling.md`).
*   Chunk progress lines are not shown when going through the server (single POST). French LanguageTool checks cost about 1.7 s each time.
*   A trailing-hallucination filter ("Thank you.", "Sous-titres réalisés par…") is still to do; the dictionary has no such rules. Punctuation backlog: `M0-audit.md` §7.
*   Still open from the M0 audit (details in `M0-audit.md`): D14 (resident `whisper-server`), D15/D16 (config sources and validation), D24 (library code calling `sys.exit`/`typer.Exit`), D26 (`REPO_ROOT` assumptions; non-editable installs break), D29 (LanguageTool language map), D31 (dependency hygiene). `optimization/` still uses the legacy `core/record_audio.py` and its own copy of the chunk-cut logic.
*   `CLAUDE.md` §5 and this table are the first places to update when work advances.

## Log

- **2026-10-08** — M0 audit carried out. 31 findings are cataloged in `M0-audit.md`, along with the proposed architecture and the open decisions. Recording-control rule changed: explicit keys replace Ctrl-C/Enter. New goal added: write and transcribe audio in chunks *during* recording.
- **2026-10-08** — M0 decisions recorded (keymap, Tailscale-only bind, no permanently resident model, server at boot). M0.5 started.
- **2026-10-08** — M0.5 first pass: test suite repaired (48 passing, live tests marked), and D01–D04, D07, D10, D17–D19, D21–D23 and D27 fixed test-first, plus the Tailscale-only bind. See `M0.5-stabilize.md`.
- **2026-10-08** — M0.5 merged to `main`. Lone filler segments kept. The tool install is editable. M1 started on branch `m1-session`.
- **2026-10-08** — M1: `--file`, recording keys and pause/resume landed. A live-test quality drop was traced to GSM phone interference (not the 16 kHz format), and an automatic 217 Hz buzz detector and filter was added.
- **2026-10-08** — Chunking experiment, 2 rounds on 15 takes: chunks of ≥20 s cut at the longest Silero pause, with a style prompt, give WER 7.4 % (whole file: 7.5 %), better punctuation, and a 4× shorter wait after stop. M1b design written.
- **2026-10-09** — M1b implemented: live chunked transcription, locally and on the server. Real run: 81 s dictation, 4.5 s wait after stop (14.7 s when everything runs after stop), WER 2.4 %.
- **2026-10-09** — Quiet terminal output: one line per chunk plus the final text, with the details in `session.log` (`-v` shows them). S6 decisions: start at boot, Tailscale-only bind. Spotting transcription anomalies such as "TaylorScale" is deferred to a later pass over past transcriptions.
- **2026-10-09** — S6 host side: boot-time systemd user service with lingering, Tailscale and localhost bind (waits for Tailscale), a client filter limited to Tailscale and localhost, and the host CLI on localhost. Verified per interface, including from a Docker container. Remaining for M1: a reboot test and the client role.
- **2026-10-09** — Client role moved to the new recorder and keys, sharing the host's server path. M1 is complete except for a reboot test of the service.
- **2026-10-15** — M7 specified: other projects get a Python `dictate()` plus a thin `answer` CLI (same package, server unchanged, no notification layer). Schema profiles become data (`schema: module:Class` plus a generic `SchemaPipeline`). Agent guide written in `skills/integrating-dictation-hub/`; the project's other embedded skills were removed from the working tree by the user.
- **2026-10-15** — M7 decisions closed: subcommand `answer`; transcription is always the server's `standard` profile; the caller's schema step runs on the calling side. Tickets 1–2 done test-first in `src/dictation_hub/api.py` (`extract`, `dictate` on an audio file, host and client role; 9 tests in `tests/test_api.py`). Next: `answer -f` (ticket 3).
- **2026-10-15** — M7 ticket 3: `dictation-hub answer -f clip.wav [--profile-file p.json --schema mod:Class]` prints one JSON document (no `--json` flag: it always does), exit codes 0/1/2/4, nothing on the clipboard, pipeline output redirected to stderr. Code in `answer_cli.py`, registered with two lines in `cli.py`. Next: live recording (ticket 4).
- **2026-10-15** — M7 done: `answer` records live (server first, local fallback; client role needs the server), exit 3 on cancel/quit, tickets 4–5 finished and the skill switched from planned to current. A test caught a leftover spool file after a cancel through the server. 198 tests pass.
- **2026-10-15** — M7 follow-ups: Mistral-first defaults for the LLM step (no profile needed), early `MISTRAL_API_KEY` check, retry with error feedback, fenced-JSON tolerance, detected language in the prompt, `schema` key in profile files. Skill extended with `llm-step.md` and `lessons-from-scheduling.md`. 208 tests pass.
- **2026-10-15** — Everything pushed to `origin/main`; `m1-session` merged and deleted. Documentation brought up to date for resuming: `CLAUDE.md` (fourth entry point, status line, M7, skills section, cheatsheet without unimplemented flags, new §6 "Resuming Work" with repo map, conventions, gotchas) and a "Next up" list in this file.
- **2026-10-19** — Fix: recording failed at once on WSL2. The M1 recorder hardcoded `arecord`, which finds no sound card under WSLg (the mic is PulseAudio-only), and the old client's backend probing had been removed with `client/streamer.py`. `core/recorder.py` now has `capture_command()` (override → `arecord` if ALSA has a card → `parec` → `ffmpeg` pulse/avfoundation, clear `CaptureUnavailable` message if none), and a dying capture process's stderr is shown instead of discarded. Tests first (4 new, 212 pass); verified with a real 2 s capture on WSL. `arecord_command` was renamed `capture_command`.
- **2026-10-15** — Provider default decided: stays Mistral, because Ollama may not be installed. A future Ollama-availability check may choose the default per machine.
- **2026-10-20** — Open issue: `server stop` hung. On the host, `systemctl --user stop dictation-hub` logged uvicorn's "Waiting for connections to close", then systemd SIGKILLed it after its 90 s timeout; `start` blocks behind it. The remote client also failed around then (probably while the server was shutting down), but worked again after the restart. **Cause not found**: a WebSocket, a keep-alive connection and a stalled upload all let the server exit in 0.2 s, and no connection was open when checked afterwards. Mitigation, untested against the real hang: `serve()` sets `timeout_graceful_shutdown=5` (`GRACEFUL_SHUTDOWN_S`). If it happens again, capture `ss -tnp | grep :8000` and `journalctl --user -u dictation-hub -n 30` while it is stuck.
