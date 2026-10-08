# M1 — Session, Recording Controls, `--file`, Boot-time Server

- **Started:** 2026-10-08 (branch `m1-session`)
- **Status:** 🟡 In progress
- **Approach:** vertical TDD slices. Each one is a failing test at a public seam, then the minimal code to pass it.

## Slices
| # | Slice | Seam under test | Status |
|---|-------|-----------------|--------|
| S1 | `--file/-f PATH` reprocesses an existing recording on the host. The file is copied untouched into the workspace as `<ts>_capture.<ext>`, then normalized and transcribed. No recording happens. | CLI → runner args; runner with fake whisper/pipeline and real ffmpeg | 🟢 |
| S2 | `--file` in client mode uploads the file to the server instead of the mic stream. | `run_remote_stream` with fake upload | 🟢 |
| S3 | Recorder captures 16 kHz mono directly, in its own process group (terminal signals never reach it). It is stopped with SIGINT so the WAV header is finalized. | Recorder with a fake capture command | 🟢 (needs a live try) |
| S4 | Recording keys: recording starts at launch. **Space** = stop and process, **p** = pause/resume, **q**/**Ctrl-C** = quit and keep raw audio, **c** = cancel, which removes the workspace. | Key controller driven by a scripted key source | 🟢 (needs a live try) |
| S5 | Shared `Session` orchestrator used by the local runner and the server, which removes the duplicated flow (D20). | Session with fake transcriber and pipeline | ⚪ |
| S6 | Boot-time service: a `systemd --user` unit for the server and the LanguageTool daemon, set up with `dictation-hub server install-service`. The host CLI uses the server when it is up and otherwise runs in-process. `HOST_BIND_*` gets wired up or removed. | Routing decision with a fake health probe | ⚪ |

Deferred to **M1b**: chunked capture, incremental transcription, and the model loaded only while recording.

## Log
- **2026-10-08** — Plan written. Lone filler segments are kept (decision). The `uv tool` install is now editable from this checkout.
- **2026-10-08** — S1 and S2 done. Real run: `dictation-hub -f optimization/dataset/03_beer_article_extract/take_01.wav standard -l en` took about 10 s end to end, kept the untouched capture, and wrote correct artifacts. Bug noticed: the clipboard helper reports success even when `xclip` fails (fold into D28).
- **2026-10-08** — S3 and S4 done in `core/recorder.py`. `arecord -f S16_LE -r 16000 -c 1` runs in its own session and is stopped with SIGINT. Pause/resume closes and opens segments, which are joined on stop. Keys are read in cbreak mode. Outcomes: process / quit (keep `<ts>_capture.wav` and print the `-f` command) / cancel (remove workspace) / failed (exit 1). The ALSA `default` device is PipeWire, which resamples. Not yet tried interactively with the real mic. `optimization/record_dataset.py` still uses the old `record_audio_app`.
