# M7 — External Integration API (`dictate()` + `answer`)

- **Status:** 🟢 Implemented 2026-10-15 (tickets 1–5). Merged to `main` and pushed 2026-10-15.
- **Motivation:** other projects (first case: a data-annotation tool that asks a question, the user answers aloud, a Pydantic-validated record comes back) must be able to use Dictation-Hub for recording, transcription and schema extraction. Today the only non-interactive seam is `POST /transcribe`, which returns an unvalidated JSON string and only knows profiles registered on the host.
- **Agent guide:** `skills/integrating-dictation-hub/` (written ahead of the code; its `[M7]` sections describe this milestone).

## Decisions (2026-10-15)

1. **One package, no second distribution.** `dictation_hub.api` is a module in the existing package, installed into other projects with `uv add --editable <path>`.
2. **Thin CLI named `answer`.** `dictation-hub answer` records the user's spoken answer to a question the calling tool has *already asked* (so no `--prompt`), then prints one JSON document on stdout. Progress and logs go to stderr. No clipboard. `run` stays untouched (it is already the router's hidden default command).
3. **Transcription is always the server's `standard` profile.** An external tool never gets whisper settings of its own. The text it receives is the scrubbed, grammar-checked transcript, through the same path as `dictation-hub standard` (server first, local fallback as today).
4. **Post-processing is the caller's own, and runs on the calling side.** If the caller supplies a profile (prompt, model) and a Pydantic `schema`, one schema-enforced LLM call turns the transcript into validated data. Without a schema, the result is just the text (`data` is null, `ok` true). The server stays unchanged and needs no knowledge of external profiles.
5. **Schema step is a function, not a pipeline class.** `api.extract(text, profile, schema)` does the LLM call and validation; no `PIPELINE_MAP` entry, no cleaner (the text is already cleaned).
6. **Profile outside `~/.config`.** `--profile-file` / `profile=<path>` takes one profile object (only its `post_processing[0]` is used); the name is the file stem. The schema is given with `--schema module:Class`.
7. **Failure policy unchanged** (invariant 4): an LLM or validation failure returns the cleaned transcript with `ok: false` and exit code 4.
8. **No notification layer.** The caller blocks until the result.
9. **Deferred:** decoupling config and `repo_root` from the source tree (non-editable installs), multi-step chains.

Result type and exit codes: `skills/integrating-dictation-hub/contract.md`.

## Tickets (tracer bullets, TDD)

Seams: `api.extract()` and `api.dictate()` in Python, and `dictation-hub answer` through Typer's `CliRunner`. At these seams the LLM provider and whisper are faked; no test needs a mic, GPU or model.

| # | Ticket | Blocked by | Done when |
|---|---|---|---|
| 1 | `extract(text, profile, schema)` with `module:Class` resolution | — | Valid LLM JSON gives `ok` and a model instance; invalid JSON or a schema violation gives `ok=False`, `data=None`, text kept; a bad import path raises a clear error before any LLM call. |
| 2 | `dictate(...)` on an audio file: standard transcription, then `extract` | 1 | Returns the full result type (`raw_text`, `final_text`, `data`, `workspace`, `audio`); no schema gives text only; unknown profile file raises before any work. |
| 3 | `answer -f clip.wav` | 2 | stdout is one JSON document and nothing else; exit codes 0/2/4; no clipboard write. |
| 4 | Live recording in `answer` (recorder keys, exit 3 on cancel) | 3 | Cancel and quit give exit 3 with an error JSON (audio path kept on quit); a dead mic gives exit 1; the server is used first with local fallback. Without a TTY the recorder reads keys from stdin lines, so no refusal is needed. |
| 5 | Docs: README section, flip the `[M7]` markers in the skill to `[today]` | 4 | The skill matches the code. |

`-f clip.wav` is for tests and retrying a saved capture; by default `answer` records the user.

## Additions after the five tickets (2026-10-15)
- **Defaults for the LLM step (Mistral; preferred by the user only for large loads and complex tasks, see README "Next up"):** no profile is needed. `provider` defaults to `mistral`, `model` to `mistral-small-latest` (`qwen2.5-coder:7b-instruct` for `local`), plus a generic extraction prompt; every key of `post_processing[0]` is optional. A missing `MISTRAL_API_KEY` is bad input (exit 2) and is checked before recording. The key line was added to `configs/config.env.template`.
- **Retry with feedback** (`retries=2`): a rejected answer is retried with the validation error appended to the input, the idea from the scheduling pipeline. Fenced ```json answers are accepted.
- **Language:** the language whisper detected reaches the prompt's `{language}` (`auto` when the server transcribed, since it does not return it yet). `language` is in the result.
- **`schema` in the profile file** is read (the argument wins).
- Insights from the scheduling tool are written up in `skills/integrating-dictation-hub/lessons-from-scheduling.md`.
- Open: have the server return the detected language; the two "now"s problem (prompt context uses today, scheduling resolves against the recording time).
