# Milestones — Status & Progress Log

The roadmap lives in `CLAUDE.md` §2. This folder tracks **progress**: what was done and what was decided, with one file per milestone.

| ID | Title | Status | Doc |
|----|-------|--------|-----|
| M0 | Audit, defect catalog, architecture proposal | 🟢 Done | [M0-audit.md](M0-audit.md) |
| M0.5 | Stabilize: repair tests, fix high-severity defects test-first | 🟡 In progress | [M0.5-stabilize.md](M0.5-stabilize.md) |
| M1 | Session orchestrator, recording controls, 16 kHz capture, `--file`, server autostart/fallback | ⚪ Not started | — |
| M1b | Chunked capture & incremental transcription (VRAM-frugal) | ⚪ Not started | — |
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
