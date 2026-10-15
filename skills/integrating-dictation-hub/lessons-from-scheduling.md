# Lessons from the scheduling tool

Source: `src/dictation_hub/pipeline/pipelines/scheduling.py`, `tests/test_scheduling*.py`. It is the most developed schema tool in the repo, and its quirks show what to do and avoid when building another.

## What it does well (copy this)

1. **The LLM extracts words, code computes values.** `LLMEventExtraction` asks only for literal phrases (`raw_start_date="next Tuesday"`, `raw_start_time="1.30pm"`, "extract the exact text, do NOT convert to 24-hour format"). `dateparser` plus a few rules produce the ISO datetimes. A 7B model is unreliable at date arithmetic; it is good at quoting.
2. **Two models, not one.** The *extraction* schema (what the model fills) differs from the *output* schema (`ScheduleEvent`: resolved datetimes, duration). Do not make the model fill fields you can derive.
3. **Descriptions carry the instructions**, with examples in both languages (`'mardi prochain'`, `'14h'`) and explicit negatives ("Do NOT include times", "Do NOT use this for '6 months'").
4. **Validators that explain themselves.** `check_cross_field_logistics` rejects `recurring_event` without `recurrence`; the message is what the model sees on retry.
5. **Retry with the error fed back** (3 attempts, `[SYSTEM ERROR IN PREVIOUS PASS]` appended to the input). `api.extract` now does the same for any schema (`retries=2`).
6. **Failure raises** (`SchedulingError`); the generic `run_post_processing` turns that into the scrubbed text. Never return an error payload as if it were a result.
7. **The deterministic part is callable without an LLM** (`parse_temporal_components`), so it can be tested with a frozen clock.

## Quirks and traps

- **Two different "now"s.** `call_llm` puts the *current* date into `CURRENT_CONTEXT`, but the pipeline resolves relative dates against the *recording's* timestamp from `metadata.json`. Reprocessing an old file (`-f`) therefore gives the model today's date and Python the old one. `answer` has no recording timestamp at all: if a tool resolves relative dates, pass an explicit reference date yourself and decide which clock rules.
- **Heuristics pile up.** Date phrases are patched with: strip "next/prochain", roll a past date forward 7 days, morning = 08:00, afternoon = 13:00, evening = 18:00, end defaults to start + 1 h, midnight → noon, overnight → +1 day, AM/PM flip. Each patch fixed a case and may break another. Add a test for every new rule, and prefer asking the speaker-facing field for the literal phrase over adding another guess.
- **Stripping substrings blindly** (`"next"` removed from any phrase) can mangle words that contain it; match whole words when you copy the idea.
- **`dateparser` is language-sensitive.** It is called with the detected language first, then without. The language comes from whisper's detection (`metadata.json`); a wrong detection silently degrades parsing.
- **Time of day lives in two fields** (`raw_start_date` may hold "tomorrow morning" while `raw_start_time` is empty). Decide where a concept lives and say so in both descriptions.
- **Tests that need Ollama.** `tests/test_scheduling.py` is `@pytest.mark.live`; only `test_scheduling_failures.py` stubs `call_llm`. Prefer the second style: stub the LLM, assert on the deterministic code, keep a small live set for prompt regressions.
- **Dead branch.** After the retry loop there is an unreachable `return raw_output`; with a loop that either returns or raises, do not add a fallback that returns unvalidated model output.
- **Hard-wired registration.** The pipeline works only because `"scheduling"` is in `PIPELINE_MAP` and the profile exists in `pipeline_config.json` with the same name. A name mismatch silently runs `standard`.
- **Webhook payload = the final JSON string.** `dispatch_webhook` parses `final_text` as JSON and falls back to `{"text_content": …}`; a schema tool that feeds n8n must return valid JSON from `execute`.

## Checklist for a new schema tool

1. Write the schema with literal-quote fields; compute derived values in Python.
2. Give every field a description with an example and a "null if absent" rule.
3. Put cross-field rules in a `model_validator` with a message a model can act on.
4. Test the validators and the Python post-computation without any LLM; fake the provider for the flow; add at most a few live tests.
5. Decide which clock and which language the post-computation uses, and write it down in the schema docstring.
6. Run it with a small Mistral model first; move to `mistral-large-latest` only if retries show real reasoning failures.
