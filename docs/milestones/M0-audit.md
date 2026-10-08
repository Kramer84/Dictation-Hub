# M0 — Audit, Defect Catalog & Architecture Proposal

- **Date:** 2026-10-08
- **Status:** 🟢 Audit complete. Decisions recorded in §6.
- **Commit audited:** `eb4beb9`

## 1. Method & environment snapshot

I read every module under `src/dictation_hub/`, plus `configs/`, `tests/` and `pyproject.toml`. I confirmed CLI behaviour with `typer.testing.CliRunner` and ran `pytest`.

The host environment as of the audit:

| Item | Value |
|------|-------|
| GPU | NVIDIA GTX 1070, 8 GB, driver 580.178 |
| whisper.cpp | `~/whisper.cpp`, built with CUDA (`whisper-cli` links `libggml-cuda`, `libcublas`). `whisper-server`, `whisper-stream` and `whisper-vad-speech-segments` are also built. |
| Models | `large-v3`, `large-v3-turbo`, `large-v3-turbo-q5_0` (default in `standard.env`), `small`, `base.en`, `silero-v6.2.0` |
| Display | X11 session. `xclip` and `xsel` present. `xdotool`, `wtype` and `wl-copy` **absent** |
| Python | 3.10 via `uv` |

## 2. Defect catalog

Severity: **H** = broken or unsafe, **M** = wrong or fragile, **L** = cleanup.

### CLI & routing
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D01 | H | **Every extra flag is rejected** (exit 2): `standard -l fr`, `standard --language fr`, `-l fr standard`. The hidden `run` command has no `context_settings`, so `ctx.args` is always empty. As a result, the override parsing in `local_runner` and `streamer.build_url` is dead code. *(Confirmed with CliRunner.)* | `cli.py:101` |
| D02 | M | The override parsers only understand `--key value`. They miss short flags (`-l`), `--key=value`, and negative numbers (`--logprob_thold -0.5` is read as a flag). | `local_runner.py:30`, `client/streamer.py:build_url` |
| D03 | M | `valid_arguments` is only `language/model/temperature`. Anything else (e.g. `--threads`, shown in the cheatsheet) only triggers a warning and is dropped. | `configs/pipeline_config.json` |
| D04 | L | An unknown profile silently falls back to `standard` in the local runner, the server and the engine. A typo therefore runs the wrong pipeline without an error. | `local_runner.py`, `server/main.py`, `engine.py` |
| D05 | L | In client mode, the `--ip/--port/--protocol` options documented in the cheatsheet can't be reached. Only environment variables work. | `cli.py:main_run` |
| D06 | L | Shell completion only offers profile names. Flags are never completed. | `cli.py:shell_complete` |

### Audio capture
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D07 | H | **The true raw recording is deleted locally.** `arecord` writes `.tmp.wav`, which is normalized into `<ts>_raw.wav` and then unlinked. The file *named* `_raw.wav` is actually the processed audio. This rules out re-normalizing or reprocessing from the original. | `core/record_audio.py:140` |
| D08 | M | The Enter/Ctrl-C stop is ad hoc. SIGINT reaches the whole foreground process group. In client mode, Ctrl-C also kills `curl`, so the upload is truncated or the response is lost. Ctrl-C during whisper or the LLM aborts with nothing saved. | `record_audio.py:record_audio`, `streamer.py:wait_for_user_stop` |
| D09 | M | Two-pass **whole-file** peak normalization needs the complete file before anything can run, which blocks chunked processing. It also costs two extra ffmpeg passes, and one loud click sets the gain for the whole recording. | `record_audio.py`, `server/main.py` |
| D10 | H | The client runs `killall ffmpeg arecord rec parecord` at startup. That **kills unrelated user processes**, such as a running video encode. | `client/streamer.py:14,191` |
| D11 | L | `arecord -f cd` records 44.1 kHz stereo, which is then resampled. Recording 16 kHz mono directly (whisper's native input) would skip that pass. | `record_audio.py` |
| D12 | M | There is no way to reprocess an existing file (`--file`). | — |

### Whisper / GPU
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D13 | H | **There is no GPU check.** `whisper-cli` silently runs on CPU if CUDA init fails. Its stderr isn't captured, so the backend line ("no GPU found" or the CUDA device) is never inspected. | `core/whisper_interface.py:316` |
| D14 | M | `whisper-cli` reloads the model on every run. `whisper-server` is already built and would keep the model in VRAM. That matters even more once audio is processed in chunks. | `whisper_interface.py` |
| D15 | L | There are several sources of truth for defaults. `whisper_transcribe` defaults (`large-v3`, 4 threads) differ from `standard.env`. `WHISPER_DIR` is in both `config.env` and `standard.env`. Config is passed around as a 60-parameter Typer signature through `locals()`. | `whisper_interface.py` |
| D16 | L | Values in the env file are coerced with bare `int()`/`float()`. A malformed value causes a raw traceback, and there is no validation. | `whisper_interface.py:apply_config_overrides` |

### Server
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D17 | H | **Blocking work runs inside `async` endpoints.** ffmpeg, whisper and the LLM run on the event loop, so the server handles one request at a time and the web UI and WebSocket freeze. | `server/main.py:142,157` |
| D18 | H | **Path traversal.** `profile` from the query string or WebSocket goes straight into the workspace path (`profile=../../x`). The server binds `0.0.0.0` by default and has no auth. | `server/main.py:146,175` |
| D19 | M | The server reads `language` from the top level of whisper's JSON, where it is always `auto`. The local runner correctly reads `result.language`. As a result, on the server path **LanguageTool always runs `en-US`**, even on French text. | `server/main.py:95` vs `core/utils.py:17` |
| D20 | M | `server/main.py:execute_pipeline` and `core/local_runner.py` duplicate the whole orchestration, and they have already drifted apart (D19, error handling). | both |
| D21 | L | All query params, including `profile`, are passed as whisper overrides, which produces spurious warnings. | `server/main.py` |

### Post-processing pipeline
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D22 | H | **There is no fallback when the LLM fails.** Any exception makes the local runner exit 1 without copying the scrubbed text, and the server returns a 500. The degradation rule is not implemented. | `local_runner.py:125`, `server/main.py:118` |
| D23 | M | When scheduling fails, it returns `{"error": ..., "raw_output": ...}` as the *final text*. That is copied to the clipboard and **POSTed to the n8n webhook** as if it were valid. | `pipelines/scheduling.py:550` |
| D24 | M | Library code calls `sys.exit` and `typer.Exit`. Inside the server this ends requests, or worse. | `text_tools.py:465`, `whisper_interface.py`, `n8n_dispatcher.py` |
| D25 | M | Every pipeline copy-pastes the same cleaner → regex → grammar steps. The dictionary path is resolved against `repo_root`, so the user's `~/.config/dictation_hub/hallucinations_dict.yaml` is **ignored**. `_raw.txt` is hard-coded instead of using `suffixes.raw_text`. | `pipelines/*.py`, `base.py:90` |
| D26 | M | `REPO_ROOT = parents[3]` is assumed in three places, and `configs/*.env` is read from the repo. A non-editable install breaks. `engine.main` reads repo configs while the CLI reads `~/.config`. | `config_manager.py:11`, `server/main.py:21`, `engine.py:88` |
| D27 | L | `n8n_dispatcher.main()` uses `os` without importing it, which raises `NameError`. | `server/n8n_dispatcher.py:48` |
| D28 | L | The clipboard helper is duplicated. The local copy is missing `pbcopy` and the client copy has it. | `core/utils.py`, `client/streamer.py` |
| D29 | L | `LANG_MAP` covers only en/fr/de, and `auto` maps to `en-US`. | `text_tools.py` |

### Tests & hygiene
| ID | Sev | Finding | Location |
|----|-----|---------|----------|
| D30 | H | **The test suite doesn't run.** `tests/test_cleaner.py` imports `post_processing.deterministic_cleaner` from an old layout, so collection fails and the whole `pytest` run aborts. `test_scheduling.py` is a **live-LLM** test that hangs for more than 2 minutes without Ollama. There are no CLI, audio, config or server tests. | `tests/` |
| D31 | L | `pytest`, `freezegun` and `ipython` are runtime dependencies, and `python-dotenv` is listed twice. The package name `speech-to-text-transcription-tool` doesn't match `dictation_hub`. `autotest.toml` has the wrong `import_prefix`. | `pyproject.toml`, `autotest.toml` |

## 3. Architecture proposal

### 3.1 One orchestrator, three thin adapters
The biggest structural problem is that each entry point re-implements the flow (D20). I propose a single session orchestrator that takes an **audio source**:

```
AudioSource = LiveMic | AudioFile (--file) | NetworkStream (HTTP/WS)
                      │
               Session.run(source, profile, overrides) -> SessionResult
                      │
   capture/chunk → ASR (per chunk) → merge → scrub → LLM → deliver
```

The local CLI, the server and `--file` all become small adapters around `Session.run`. `SessionResult` records the status of each stage, which is how graceful degradation (D22/D23) gets implemented once, in one place.

### 3.2 Target package layout (built up step by step, not as a big-bang rewrite)
```
src/dictation_hub/
  cli/        app.py (Typer), completion.py
  config/     settings.py  – Pydantic: DeploymentSettings, WhisperSettings, Profile, PipelineConfig
              loader.py    – user dir (~/.config) overrides packaged defaults; no REPO_ROOT
  audio/      recorder.py  – subprocess in its own session, explicit start/stop, chunk callback
              chunker.py   – silence-aware segmentation of a PCM stream
  asr/        base.py      – Transcriber protocol
              whisper_server.py (resident model), whisper_cli.py (fallback), gpu.py (pre-flight)
  session/    workspace.py (paths/manifest/metadata), orchestrator.py
  pipeline/   existing; shared Scrubber stage, profiles only declare their LLM step
  output/     clipboard.py, inject.py (xdotool/wtype), webhook.py
  server/     FastAPI adapter → orchestrator in a threadpool
  client/     streaming adapter
```

### 3.3 Chunked recording & incremental transcription (new goal)
The aim is for the time between pressing stop and getting the result to stay the same however long the dictation was.

1. The recorder captures **16 kHz mono PCM** directly and passes frames to a chunker. The full recording is also written continuously to `<ts>_raw.wav`, so raw audio is never lost.
2. The **chunker cuts at silences**: it cuts once a chunk is at least ~15 s long and there is ≥ 400 ms of silence, with a hard cap at about 30 s (whisper's window). Chunks are written as `chunks/000.wav`, `001.wav`, … I'll start with a simple energy-based VAD and only switch to Silero if needed.
3. A **worker thread** sends each closed chunk to a **resident `whisper-server`**, which avoids reloading the model each time. It passes the end of the previous chunk's text as `--prompt` for continuity. If language is `auto`, it fixes the language after the first chunk so it can't flip between chunks.
4. On stop, the last chunk is flushed and the per-chunk JSONs are merged with time offsets into one `<ts>_full.json`, in the same shape `whisper-cli` produces. The scrubber and pipelines don't change.
5. **Normalization:** whole-file peak gain goes away (D09). Peak gain per chunk, or none at all, is decided by measuring WER on `optimization/dataset/` (the `jiwer` tooling is already there).
6. The server path uses the same chunker on incoming bytes. The HTTP stream is already written to disk as it arrives, and WebSocket webm needs an ffmpeg decode pipe. `--file` runs the same chunker over a file.

Risks: words cut at chunk boundaries (mitigated by silence-based cuts plus a small overlap), and the extra complexity of a worker thread. Fallback: if `whisper-server` isn't available, use `whisper-cli` per chunk, or the whole file in one run.

### 3.4 Recording controls
- The terminal goes into cbreak mode and reads single keys. Proposed keys: `s`/Space = stop and process, `p` = pause/resume, `q` = quit (keep raw audio, skip processing), `c` = cancel (discard).
- Recorder subprocesses are started in their own session (`start_new_session=True`) so terminal Ctrl-C never reaches them. We stop them ourselves with SIGINT so the WAV header is finalized.
- Ctrl-C means a clean quit that keeps the raw audio. It no longer has to mean "transcribe".
- For hotkeys without a terminal (M4), a `dictation-hub toggle` command signals the running session through its PID file.

### 3.5 Error policy
- Library code raises typed exceptions (`ConfigError`, `GpuUnavailable`, `TranscriptionError`, `LLMError`). Only the CLI layer turns them into exit codes, which fixes D24.
- If the LLM fails, final text = scrubbed text plus a warning, and it is still copied to the clipboard. Webhooks are **not** fired with error payloads (D23).
- If the GPU is missing (M2), the run stops with a clear message unless the user passes `--allow-cpu` or picks a small model.

## 4. Proposed test seams (TDD)
Following `skills/tdd`, tests go only at agreed public seams. These are the seams I propose, all runnable without a mic, GPU or LLM:

1. **CLI parsing:** argv → `Invocation(profile, overrides, source, outputs)`, tested with `CliRunner`.
2. **Config resolution:** base env + profile overrides + CLI overrides → `WhisperSettings` (validated), then → whisper command line.
3. **Chunker:** synthetic PCM (tone/silence patterns) → chunk boundaries.
4. **Session orchestrator:** with a fake `Transcriber` and fake LLM, check that a file goes in and the expected workspace artifacts and degradation behaviour come out.
5. **Scrubber:** repair the existing `test_cleaner.py` and keep it.
6. **Server:** `TestClient` POSTs a small WAV with a fake transcriber. This also covers the path-traversal fix.

Live tests (real whisper, GPU or Ollama) are marked `@pytest.mark.live` and excluded by default.

## 5. Open decisions (need your input)
1. **Keymap:** is §3.4 (`s`/Space stop, `p` pause, `q` quit-keep-raw, `c` cancel) OK?
2. **ASR backend for chunks:** resident `whisper-server` (recommended), `whisper-cli` per chunk (simpler, slower), or `whisper-stream` (true streaming, less control)?
3. **Refactor depth:** build the §3.2 layout step by step as milestones touch each area (recommended), or restructure first?
4. **Re-sequencing:** I suggest adding **M0.5 Stabilize** before M1, covering D01, D07, D10, D17, D18, D19, D22, D23, D27 and D30, each fixed test-first. Most are small. M1 would then become the orchestrator plus `--file` plus recording controls, and a new **M1b** would cover chunked transcription.
5. **Server exposure:** bind to the Tailscale IP or `127.0.0.1` by default, and/or require a shared token?

## 6. Decisions (2026-10-08)

| # | Topic | Decision |
|---|-------|----------|
| 1 | Keymap | Recording starts immediately when the tool launches. **Space** = stop and process. **p** = pause, **p** again = resume. **q** = quit without processing, keeping the raw audio so it can be reprocessed later with `--file`. **c** = cancel, which discards the audio and leaves no workspace folder behind. **Ctrl-C** = same as `q`, so no audio is ever lost to an accidental Ctrl-C. |
| 2 | Capture format | Record 16 kHz mono PCM directly. |
| 3 | VRAM | **No model resident permanently.** VRAM is limited (8 GB) and shared with local LLMs used for post-processing. Option under consideration: run `whisper-server` only *while recording* and stop it on stop, so VRAM is freed before the LLM step. Fallback: `whisper-cli` per chunk. To be decided in M1b. |
| 4 | Server exposure | Bind to the **Tailscale interface only** by default (no more `0.0.0.0`). |
| 5 | Host architecture | The server should start at boot on the host, probably as a `systemd --user` unit. The local CLI should go through the server when it is up. When it is down, it either auto-starts it or runs in-process. The exact fallback is to be decided in M1. |
| 6 | Sequencing | **M0.5 Stabilize** comes first, test-first, starting with repairing the test suite. M1 and M1b follow. |
| 7 | Refactor | Incremental: the §3.2 layout is introduced as milestones touch each area. |
| 8 | VRAM timing | The whisper model loads **when recording starts**, so it is ready for the chunks and the final flush, and is **freed before the LLM step**. |
| 9 | Desktop use | Configurable global keyboard shortcuts launch the tool in the background and type or paste the result into the active window (M4). This needs `xdotool` on X11, which is not installed yet. |
| 10 | Fillers | Phrases made only of fillers are kept. Whisper's invented trailing phrases ("Thank you.", etc.) are filtered out. |

## 7. Backlog: punctuation and intent (later phase)

Raised 2026-10-08: whisper's punctuation often misses the intent (missing marks, or sentence ends where there are none), and short chunks may make this worse. Options, cheapest first:
1. **Style prompt.** Pass a well-punctuated sentence (or the previous chunk's text) as `--prompt`. Costs nothing, and its effect can be measured with `optimization/chunking_experiment.py` (`end_f1`, `comma_f1`).
2. **Dedicated punctuation-restoration model**, e.g. a multilingual token classifier covering en/fr/de/it. Small, runs on CPU so it needs no VRAM, and is deterministic.
3. **LLM punctuation pass.** Best at intent, but it costs VRAM and latency and can rewrite words. It would have to be constrained to punctuation and case only, with a check that the word sequence is unchanged.
