# M1b — Chunked Capture & Incremental Transcription

- **Status:** ⚪ Designed from measurements, not started.
- **Evidence:** `optimization/chunking_experiment.py`, run on the 15 dataset takes (12 English, 3 French, 66–184 s each). Results are in `optimization/chunking_results.json` (round 1) and `optimization/chunking_results_round2.json` (round 2).

## Results (means over 15 takes; turbo-q5, `standard.env` settings)

| Variant | WER | Sentence-end F1 | Comma F1 | Extra stop at cut | Wait after stop | Total GPU time |
|---|---|---|---|---|---|---|
| whole (current) | 7.5 % | 0.845 | 0.731 | — | 6.9 s | 6.9 s |
| whole + style prompt | 8.1 % | 0.945 | 0.789 | — | 6.7 s | 6.7 s |
| chunks ≥10 s, first pause | 8.1 % | 0.802 | 0.718 | 8 % | 1.5 s | 14.7 s |
| chunks ≥20 s, first pause | 7.7 % | 0.890 | 0.744 | 7 % | 1.7 s | 9.7 s |
| chunks ≥20 s + previous text as prompt | 7.5 % | 0.839 | 0.638 | 6 % | 1.7 s | 9.9 s |
| chunks ≥20 s, longest pause | 7.4 % | 0.901 | 0.737 | 10 % | 1.6 s | 9.2 s |
| **chunks ≥20 s, longest pause + style prompt** | **7.4 %** | **0.927** | **0.775** | 11 % | **1.6 s** | 9.2 s |
| chunks ≥15 s, longest pause + style prompt | 7.7 % | 0.908 | 0.785 | 15 % | 1.5 s | 10.0 s |

Findings:
- **Chunks of at least 20 s lose essentially no accuracy.** In English, WER goes 5.3 % → 4.7 %. In French it goes 16.2 % → 18.4 %, but with only 3 takes that gap is uncertain. The **wait after stop drops about 4×**.
- Short chunks (10–15 s) hurt punctuation: whisper treats each chunk as a complete sentence.
- **Never pass the previous text as the prompt.** If one chunk comes out without punctuation, the prompt copies that style into every later chunk (French take 2: sentence-end F1 0.00).
- The **style prompt** (a short, well-punctuated sentence in the dictation's language) is the strongest punctuation lever, both on whole files and on chunks.
- Chunking also guards against whole-file punctuation collapses. One take had sentence-end F1 0.00 as a whole file and 0.87 when chunked.
- Extra GPU time comes from reloading the model for every chunk (about 5 chunks per take). Keeping the model loaded while recording removes it.

## Design

1. **Capture:** 16 kHz mono, written continuously to `<ts>_capture.wav` (the raw-audio rule).
2. **Cut points:** Silero VAD (`silero-v6.2.0`) pauses, gaps ≥ 0.25 s. Once a chunk is ≥ 20 s, cut at the **longest pause between 20 and 30 s**. Hard-cut at 30 s only if there is no pause. Tails under 3 s are merged into the previous chunk.
3. **Per-chunk work while recording:** GSM buzz check and notch filter, then gain, then whisper with a **style prompt in the dictation language** and no previous text.
4. **Language:** with `-l xx`, use it. With `auto`, detect on the first chunk and lock it for the rest. Open question: does the first chunk get a style prompt before its language is known? The experiment used the known language, so this is unmeasured.
5. **VRAM:** the whisper model is loaded when recording starts and freed before the LLM step (decision 8). Options: a short-lived `whisper-server`, or `whisper-cli` per chunk as a fallback.
6. **Merge:** offset the per-chunk segments into one `<ts>_full.json`, so the scrubber and pipelines are unchanged. **Boundary repair:** if a chunk ends without `. ? !`, lowercase the first word of the next chunk (examples: "…poitrine plate," ‖ "La tête…", and "…new process," ‖ "Making iron…").
7. **Remaining error:** at about 1 cut in 10, whisper adds a sentence end where the text continues ("…other countries?" ‖ "Notably France…"). Handled by the punctuation backlog (M0 audit §7).

## Possible quick win before M1b
Use the style prompt on the current whole-file path. Sentence-end F1 goes 0.845 → 0.945 and comma F1 0.731 → 0.789, at the cost of WER 7.5 % → 8.1 % (mostly French). It needs a prompt per language, so it only works with `-l` or with a language probe.
