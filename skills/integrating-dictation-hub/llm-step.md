# Constructing the LLM post-processing step

The step is one function, `dictation_hub.api.extract(text, profile, schema, user_information=None, retries=2, language=None)`. `dictate()` and `dictation-hub answer` call it after transcription. It is what you configure with a profile file and a schema; you rarely write code for it.

```
cleaned transcript ──► system prompt + context + schema ──► LLM ──► strip ``` fences ──► Pydantic validate
                                                            ▲                                  │ rejected
                                                            └── transcript + error note ◄──────┘  (≤ retries times)
                                                                                    │ still rejected
                                                                                    ▼
                                                      ok=False, final_text = cleaned transcript, error set
```

## Defaults (every key of the step is optional)

| Key | Default | Notes |
|---|---|---|
| `provider` | `"mistral"` | `"mistral"` needs `MISTRAL_API_KEY` in `config.env` or the environment; `"local"` is Ollama and needs no key. |
| `model` | `mistral-small-latest` (mistral) · `qwen2.5-coder:7b-instruct` (local) | Alias names are not checked against your account. Use `mistral-large-latest` when the schema needs reasoning (dates, many fields, ambiguity). Not benchmarked here. |
| `endpoint` | `http://localhost:11434/v1/chat/completions` | Local provider only. |
| `prompt` | `api.DEFAULT_PROMPT` | Generic extraction prompt; see below. |
| `retries` (argument) | 2 | Total calls ≤ retries + 1. |

So the smallest working setup is no profile at all: `dictation-hub answer --schema my_proj.schemas:Annotation` with `MISTRAL_API_KEY` set. A missing key fails with exit code 2 *before* recording starts.

A profile only lists what it changes:

```json
{ "post_processing": [ { "model": "mistral-large-latest", "prompt": "…" } ] }
```

## What the model actually receives

1. **System message**: your `prompt` with `{language}` replaced (the language whisper detected; `auto` when unknown, e.g. when the server did the transcription), then an appended block with the current date, time, day of week, timezone and the `user_information` from `pipeline_config.json` (`CURRENT_CONTEXT`), then an appended `MANDATORY JSON OUTPUT SCHEMA` block holding `schema.model_json_schema()`.
2. **User message**: `<DICTATION>` + the cleaned transcript + `</DICTATION>`. On a retry, the previous validation error is appended after the transcript as `[SYSTEM ERROR IN PREVIOUS PASS]: …`.
3. Temperature 0.

Consequences:
- Do not paste the schema, the date or the user profile into the prompt: they are added for you.
- The prompt should say what the fields *mean in the speaker's world* and that the dictation is data, not an instruction. `DEFAULT_PROMPT` does both; override it only when the task needs more.
- `{language}` is the only placeholder.

## Provider differences that matter

- **Mistral** only gets `response_format: json_object`, i.e. "valid JSON", **not** your schema. The schema reaches the model only as text in the system message. Enum values, required fields and ranges are therefore enforced by Pydantic after the fact, and the retry loop is what fixes mistakes. Write `Field(description=...)` as if it were the only instruction.
- **Local (Ollama)** also gets the schema in `response_format`, so output is more structurally reliable, but small models often wrap JSON in a ```json fence; `extract` strips fences.
- Both: a network error or HTTP failure is retried by the provider (429/5xx with backoff), and if it still fails the step degrades like a validation failure (`ok=False`, transcript kept).

## Writing the schema (this is the real work)

- Pydantic v2 `BaseModel`, importable by the caller (`module:Class`).
- A `Field(description=...)` on **every** field. Say what to extract, give a literal example, and say what to do when it is absent ("null if the speaker did not say it").
- `Literal[...]` for closed sets; the validation error then names the allowed values, which is exactly what the retry shows the model.
- `Optional[...] = None` for anything the speaker may not say, otherwise the model invents a value.
- Cross-field rules go in a `model_validator(mode="after")` that raises `ValueError` with an actionable message: the message is fed back to the model on retry.
- Keep it flat and small, especially with a 7B local model.
- Never ask the model to compute (dates, arithmetic, ids). Ask for the literal words and compute in Python afterwards; see `lessons-from-scheduling.md`.

## Failure policy

`extract` never raises for an LLM or validation problem: it returns `ok=False`, `data=None`, `final_text` = the cleaned transcript, `error="<Type>: <message>"` (invariant 4). It **raises** only for caller mistakes, before any LLM call: unimportable schema (`ImportError`/`AttributeError`), a missing Mistral key (`ValueError`). `answer` maps those to exit code 2 and the LLM failure to 4.

## Testing a new schema or prompt

No test needs a network or a model: fake the provider's HTTP wrapper.

```python
from dictation_hub.pipeline.core.llm_providers import GenerationResult, MistralProvider, UsageMetrics

def fake(monkeypatch, replies):
    calls, replies = [], list(replies)
    def generate(self, messages, config=None, response_schema=None):
        calls.append(messages)
        return GenerationResult(raw_content=replies.pop(0), metrics=UsageMetrics())
    monkeypatch.setattr(MistralProvider, "generate", generate)
    return calls

def test_my_schema(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    calls = fake(monkeypatch, ['{"label": "cat", "rationale": "whiskers"}'])
    assert api.extract("It is a cat.", None, "my_proj.schemas:Annotation").data.label == "cat"
```

Test the schema's own validators directly with `Model.model_validate_json(...)` (no fake needed). Real-LLM checks belong in `@pytest.mark.live` tests. Working examples: `tests/test_api.py`.

## When a schema is not enough

If the result needs deterministic post-computation (date resolution, lookups), write a hand-made pipeline (`BasePipeline` subclass registered in `PIPELINE_MAP`, as `scheduling` does) instead of, or after, `extract`. Pipelines run only through the normal profile path on the server/host, not through `answer`. If you need `answer` to feed a computation, do the computation in the calling project on `result.data`.
