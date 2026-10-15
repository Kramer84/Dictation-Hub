# Milestones — Status & Progress Log

The roadmap lives in `CLAUDE.md` §2. This folder tracks **progress**: what was done and what was decided, with one file per milestone.

| ID | Title | Status | Doc |
|----|-------|--------|-----|
| M0 | Audit, defect catalog, architecture proposal | 🟢 Done | [M0-audit.md](M0-audit.md) |
| M0.5 | Stabilize: repair tests, fix high-severity defects test-first | 🟢 Done (merged to main) | [M0.5-stabilize.md](M0.5-stabilize.md) |
| M1 | Session orchestrator, recording controls, 16 kHz capture, `--file`, server autostart/fallback | 🟡 In progress | [M1-session.md](M1-session.md) |
| M1b | Chunked capture & incremental transcription (VRAM-frugal) | 🟢 Done | [M1b-chunking.md](M1b-chunking.md) |
| M2 | GPU pre-flight & fail-fast | ⚪ Not started | — |
| M3 | CLI router redesign & completion | ⚪ Not started | — |
| M4 | Desktop active-window injection | ⚪ Not started | — |
| M5 | Voice-prefix intent routing | ⚪ Not started | — |
| M6 | Hardening, tests, pre-commit | ⚪ Not started (tests are written alongside every milestone, TDD) | — |
| M7 | External integration API: `dictate()` + `answer`, schema profiles as data | 🟢 Done | [M7-external-api.md](M7-external-api.md) |

Legend: ⚪ not started · 🟡 in progress / blocked on decision · 🟢 done

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
