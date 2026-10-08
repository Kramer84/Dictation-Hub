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
- **2026-10-08** — **Live test regression.** In a 68 s dictation, the speech between 3 and 21 s came out as only "The true output for my answer here." The capture was intact: no gaps, no clipping, and the 25–40 s quiet stretch is just a pause in speaking. But that stretch is about 10 dB louder than the user's usual speech (−17 vs −27 dB RMS) with low-frequency-heavy energy (zero-crossing rate about 400–650 against the usual 1500–2200). Isolated, it is unintelligible to every whisper setting tried, including `large-v3` with VAD off, so it is degraded audio rather than a whisper setting. The only capture change in M1 was direct 16 kHz mono, so the default **went back to `-f cd`**, with the conversion done by `prepare_audio`. `optimization/ab_capture.sh` records both formats at the same time to settle whether the format or the acoustics (mic distance) caused it.
- **2026-10-08** — **Root cause: GSM phone interference.** `ab_capture.sh` gave identical transcripts and levels for CD-format and direct 16 kHz capture. So the format was not the cause, and **direct 16 kHz mono capture is restored**. The bad recording has the GSM/TDMA signature: peaks at 217, 434 and 651 Hz, about 15 dB above the neighbouring frequencies. Removing the 217 Hz harmonic series recovers the lost speech. Measured on 8 dataset takes, the filter changes WER by at most ±1.5 points on clean audio (one take improved 0.35 → 0.12 by chance; it has no buzz). `prepare_audio` now measures `gsm_buzz_level` (clean ≤ −0.4 dB, bad +11.9 dB) and, above 5 dB, warns and applies the notch filter. The server now uses `prepare_audio` too. Limitation: detection runs over the whole file, so a short burst in a long recording can be diluted. Per-chunk detection comes naturally with M1b.
- **2026-10-08** — Pulled forward from M3 (D06): context-aware shell completion. `-`/`--` lists `-f/-l/-m/-t` and every whisper option. `-l` offers language codes, `-m` the models installed in `WHISPER_DIR/models`, and `-f` falls back to shell path completion. Profiles are offered only until one is typed. The logic is the pure function `complete_invocation`; the existing bash completion script needs no reinstall.
- **2026-10-08** — Style-prompt quick win shipped (see M1b doc). With `auto`, a language probe (about 1.5 s) runs before transcription. Next: S5.
