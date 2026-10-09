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
