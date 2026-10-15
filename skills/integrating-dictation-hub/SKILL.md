---
name: integrating-dictation-hub
description: How to call Dictation-Hub from another project (e.g. a data-annotation tool that asks a spoken question and needs structured output back), and how to build the LLM post-processing step (schema, prompt, Mistral/Ollama defaults, tests). Use when wiring Dictation-Hub into an external pipeline, adding a profile or writing a Pydantic schema tool.
---

# Integrating Dictation-Hub into an external pipeline

Dictation-Hub turns speech into text, then runs a **profile** (deterministic scrub, then an optional LLM step). An external tool wants: *ask a question → user speaks → validated structured result back*.

Files in this skill:
- `llm-step.md` — **how the LLM step is built**: defaults and which provider to choose, what the model receives, schema and prompt rules, retries, tests. Read this before writing a schema.
- `lessons-from-scheduling.md` — what the scheduling tool teaches about building such tools, plus a checklist.
- `profiles.md` — profile file formats. `contract.md` — the result and exit codes.

## Three ways in

| Way | Use when |
|---|---|
| `dictation_hub.api.dictate(...)` (Python) | The calling tool is Python. Preferred. |
| `dictation-hub answer ...` (CLI) | Non-Python caller or shell loop. A thin wrapper over `dictate`. |
| `POST /transcribe` on the server | The caller already has a WAV (own recorder, phone, another machine) and the profile is registered on the host. |

Install into the other project: `uv add --editable /path/to/Dictation-Hub` (editable only; config is found relative to the source tree).

## Calling it

The calling tool has already asked its question; Dictation-Hub only records and processes the answer.

```python
from dictation_hub.api import dictate
result = dictate(schema="my_proj.schemas:Annotation")          # records the user; Mistral defaults
result = dictate(profile="annotate.json", audio_file="clip.wav")  # profile file, saved recording
result.ok         # False when anything after transcription failed
result.data       # Annotation instance, or None
result.raw_text   # cleaned transcript, always there after a successful transcription
result.audio      # kept recording on the machine that processed it
```

```bash
export MISTRAL_API_KEY=...        # or MISTRAL_API_KEY="…" in ~/.config/dictation_hub/config.env
dictation-hub answer --schema my_proj.schemas:Annotation
dictation-hub answer --profile-file annotate.json                # profile file names the schema
dictation-hub answer -f clip.wav --schema my_proj.schemas:Annotation   # retry/test with a saved recording
```

- stdout is exactly one JSON document (shape in `contract.md`); the recorder display, progress and logs go to stderr; nothing is copied to the clipboard.
- Exit codes: 0 ok · 1 recording/transcription failed · 2 bad input (schema path, profile file, missing Mistral key; nothing recorded) · 3 cancelled or quit (`audio` set on quit) · 4 LLM step failed (JSON still printed with the cleaned text).
- Recording uses the normal recorder keys. With no TTY each line on stdin counts as one key, so a script can drive it.
- Transcription is always the server's `standard` profile (no custom whisper settings). A host processes through its running server first and falls back to itself; a client machine needs the server.
- Without a schema the result is just the text (`data` is null, `ok` true).

## Defaults at a glance

No profile needed: provider **mistral**, model `mistral-small-latest`, generic extraction prompt, 2 retries with the validation error fed back, fenced JSON accepted. Override any of it with a `post_processing[0]` in a profile file, e.g. `{"post_processing": [{"model": "mistral-large-latest"}]}`; use `"provider": "local"` for Ollama (no key). Mistral is the user's choice only for large loads and complex tasks; simple schemas should use `"provider": "local"` (see `llm-step.md`, "Which provider to choose").

## HTTP contract (for callers with their own WAV)

Server: `dictation-hub server start` (default port 8000, localhost and Tailscale peers only; others get 403).

```
POST http://127.0.0.1:8000/transcribe?profile=<name>[&language=fr]
body: WAV bytes (16 kHz mono is chunked live; other formats are converted after upload)
→ 200 {"raw_text": "...", "final_text": "...", "error": "optional"}
→ 400 {"error": "Unknown profile ..."}
```

- For schema profiles registered on the host `final_text` is a **JSON string**: `json.loads` it, then validate with your own model.
- If `error` is present the LLM step failed and `final_text` is just the scrubbed text; never parse it as JSON.
- Query overrides accepted: `language`, `model`, `temperature`.
- The profile must exist in `~/.config/dictation_hub/pipeline_config.json` on the server host. Prefer `answer`/`dictate`, which need no registration.

## Rules for code in this repo

- Tests first (`tdd`); no mic, GPU or LLM in the default run; fake the provider (`LocalProvider.generate` / `MistralProvider.generate`) as in `tests/test_api.py`; real-LLM tests get `@pytest.mark.live`.
- Deterministic scrub runs before any LLM; an LLM failure degrades to the cleaned transcript, never to an error payload posing as a result.
- Never delete `<ts>_capture.wav`; it is the raw-audio safeguard. Re-run with `-f`.
- Match surrounding style; log decisions in `docs/milestones/README.md`.

## Pitfalls

- A profile *name* missing from `PIPELINE_MAP` silently runs `StandardPipeline` (no LLM) in the normal profile path. `answer`/`dictate` with a schema don't use `PIPELINE_MAP`; use them instead of writing a pipeline unless you need Python post-computation (`lessons-from-scheduling.md`).
- `dictation-hub <profile>` (the normal command) is interactive and copies to the clipboard; automation uses `answer`.
- `{language}` in a prompt is `auto` when the server did the transcription (the server does not return the detected language yet).
- `extract()` called directly (not through `dictate`) does not load `config.env`: export `MISTRAL_API_KEY` yourself.
