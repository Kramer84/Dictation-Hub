# Profile format

## In `pipeline_config.json` [today]

Seed: `configs/pipeline_config.json`; the live copy is `~/.config/dictation_hub/pipeline_config.json` (edit that one).

```jsonc
{
  "user_information": { "location": "...", "timezone": "...", "profession": "..." },  // appended to every LLM prompt
  "valid_arguments": ["language", "model", "temperature"],                           // overrides the server accepts
  "profiles": {
    "my_profile": {
      "env": "standard.env",                       // whisper settings file in configs/
      "env_overrides": {                            // string values only
        "MARK_CONFIDENCE": "false",
        "COMPRESS_REPETITIONS": "true",
        "INITIAL_PROMPT": "domain words to bias whisper"
      },
      "dictionary": "configs/hallucinations_dict.yaml",   // regex replacements, relative to the repo root
      "webhook_url": "http://localhost:5678/webhook/x",   // optional; final JSON is POSTed, skipped when the LLM failed
      "post_processing": [                                // [] = scrub only, no LLM
        {
          "step_name": "extraction",
          "type": "llm",
          "provider": "local",                            // "local" (Ollama, OpenAI-style endpoint) | "mistral" (needs MISTRAL_API_KEY)
          "model": "qwen2.5-coder:7b-instruct",
          "endpoint": "http://localhost:11434/v1/chat/completions",
          "prompt": "System prompt. {language} is substituted."
        }
      ]
    }
  }
}
```

## As a standalone `--profile-file`

The file holds **one profile object**, nothing else; its name is the file stem. Only `post_processing[0]` is used, on the cleaned transcript. Whisper settings (`env`, `env_overrides`, `dictionary`) are ignored because transcription is always the server's `standard` profile. **Every key is optional**; defaults are in `llm-step.md` (Mistral, `mistral-small-latest`, generic prompt).

```json
{
  "schema": "my_proj.schemas:Annotation",
  "post_processing": [
    { "provider": "mistral",
      "model": "mistral-large-latest",
      "prompt": "Extract the annotation the speaker gives about the image. Fields: …" }
  ]
}
```

A local (Ollama) variant: `{"provider": "local", "model": "qwen2.5-coder:7b-instruct", "endpoint": "http://localhost:11434/v1/chat/completions"}`.

`--schema` / `schema=` overrides the file's `schema`. The module must be importable from the caller's environment. Without any profile file, `--schema` alone works.

## Schema template

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field

class Annotation(BaseModel):
    label: Literal["cat", "dog", "other"] = Field(..., description="The class the speaker states.")
    rationale: str = Field(..., description="One sentence, in the speaker's own words.")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="Only if the speaker states one.")
```

## Prompt rules
Short version (full text in `llm-step.md`): the dictation arrives as `<DICTATION>…</DICTATION>` in the user message, so say in the prompt that it is data; `{language}` is the only placeholder; the date/user context and the JSON schema are appended automatically; temperature is 0.

## Hand-written pipelines [today]
Only needed for logic a schema can't express (e.g. `scheduling`'s date parsing). Subclass `BasePipeline`, implement `execute(input_json_path) -> str`: `apply_deterministic_cleaner` → `regex_replacer` → `grammar_checker` → `call_llm(..., schema=Model)` → validate → return a string. Register the class in `PIPELINE_MAP` (`pipeline/engine.py`). Used through the normal profile path (host/server), not through `answer`.
