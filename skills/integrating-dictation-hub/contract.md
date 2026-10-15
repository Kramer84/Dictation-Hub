# Result contract

One result type, returned by `dictate()` and printed by `answer`.

```json
{
  "ok": true,
  "raw_text": "cleaned transcript",
  "final_text": "{\"label\":\"cat\",\"rationale\":\"...\"}",
  "data": { "label": "cat", "rationale": "..." },
  "error": null,
  "language": "en",
  "workspace": "/home/user/.whisper_transcriptions/20261015_120000_annotate",
  "audio": "/home/user/.whisper_transcriptions/20261015_120000_annotate/20261015_120000_capture.wav"
}
```

| Field | Meaning |
|---|---|
| `ok` | `true` only if the LLM step ran and the output validated against the schema. |
| `raw_text` | Deterministically scrubbed transcript. Present whenever transcription succeeded. |
| `final_text` | Profile output as a string. Equals `raw_text` when the LLM step failed. |
| `data` | `final_text` parsed and validated against the schema; `null` on any failure. In Python it is the model instance. |
| `error` | `null` or `"<ExceptionType>: <message>"`. |
| `language` | Language whisper detected, or `null` when unknown (server did the transcription). |
| `workspace`, `audio` | Where the untouched recording lives, for re-running with `-f`. |

Exit codes of `answer`: 0 ok · 1 recording or transcription failed · 2 bad input: profile file, schema path or missing Mistral key (nothing recorded) · 3 cancelled or quit (JSON printed with `ok: false`; `audio` set on quit) · 4 LLM step failed (JSON still printed, `ok: false`).

Callers should branch on `ok`, and on failure keep `raw_text` and `audio` for a retry rather than dropping the answer.

## Design decisions (see `docs/milestones/M7-external-api.md`)
- One package: the API is a module inside `dictation_hub`, not a separate distribution.
- The CLI adds no logic: parse flags, call `dictate`, print the result.
- No notification or callback mechanism: the caller blocks until the result.
