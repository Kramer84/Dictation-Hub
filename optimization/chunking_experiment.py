"""Does transcribing in silence-cut chunks hurt quality compared to one pass?

For every take in optimization/dataset, the audio is prepared once (as in
production) and then transcribed:

  whole            : one whisper run on the full file (current behaviour)
  whole+style      : same, with a short well-punctuated sentence as --prompt
  chunk<N>         : cut at pauses (Silero VAD) into chunks of at least N s
                     (cap 30 s), each chunk transcribed on its own
  chunk<N>+prev    : same, passing the tail of the text so far as --prompt
  chunk<N>+style   : style sentence + tail of the text so far as --prompt

The language is detected on the first chunk and locked for the next ones.
The style sentence is chosen with the take's language from metadata.json,
which simulates a user-given -l (or a language probe on the first chunk).
Settings come from configs/standard.env.

Metrics (against reference.txt):
  wer        word error rate, punctuation and case ignored
  end_f1     F1 of sentence-ending punctuation (. ? !) on aligned words
  comma_f1   F1 of commas on aligned words
  spurious   share of chunk boundaries where whisper ended a sentence but the
             reference did not
  wait_s     whisper time for the last chunk, i.e. the wait after pressing stop
             if earlier chunks were processed during recording
  total_s    total whisper time

Usage: uv run python optimization/chunking_experiment.py [--quick] [--out results.json]
"""

import argparse
import difflib
import inspect
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dictation_hub.core.record_audio import prepare_audio
from dictation_hub.core.whisper_interface import (
    apply_config_overrides,
    build_whisper_command,
    resolve_paths,
    whisper_transcribe,
)

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "optimization" / "dataset"
MAX_CHUNK_S = 30.0
PROMPT_CHARS = 200


# --------------------------------------------------------------------- whisper
def base_args() -> dict:
    sig = inspect.signature(whisper_transcribe).parameters
    defaults = {k: p.default for k, p in sig.items() if p.default is not inspect.Parameter.empty}
    defaults["input_wav"] = None
    return apply_config_overrides(REPO / "configs" / "standard.env", defaults)


def transcribe(wav: Path, args: dict, language: str, prompt: str = ""):
    run = dict(args, input_wav=wav, language=language, initial_prompt=prompt)
    out_base = wav.with_suffix("")
    cli, model_path, resolved = resolve_paths(wav, run["whisper_dir"], run["model"], out_base)
    run.update(cli_exec=cli, model_path=model_path, resolved_output_base=resolved)
    start = time.monotonic()
    subprocess.run(build_whisper_command(run), capture_output=True, check=True)
    elapsed = time.monotonic() - start
    data = json.loads(Path(f"{resolved}.json").read_text())
    text = "".join(seg["text"] for seg in data.get("transcription", [])).strip()
    return text, data.get("result", {}).get("language", language), elapsed


# --------------------------------------------------------------------- chunking
def pauses(wav: Path, args: dict, min_gap: float = 0.25):
    """[(mid, length)] of the gaps between Silero VAD speech segments, in seconds."""
    tool = Path(args["whisper_dir"]) / "build" / "bin" / "whisper-vad-speech-segments"
    model = Path(args["whisper_dir"]) / "models" / f"ggml-{args['vad_model']}.bin"
    out = subprocess.run(
        [str(tool), "-f", str(wav), "--vad-model", str(model), "-np"],
        capture_output=True, text=True,
    ).stdout
    segments = [
        (float(a) / 100, float(b) / 100)
        for a, b in re.findall(r"start = ([\d.]+), end = ([\d.]+)", out)
    ]
    gaps = []
    for (_, end), (start, _) in zip(segments, segments[1:]):
        if start - end >= min_gap:
            gaps.append(((start + end) / 2, start - end))
    return gaps


def duration(wav: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        capture_output=True, text=True,
    ).stdout
    return float(out)


def cut_points(gaps, total: float, min_len: float, min_tail: float = 3.0):
    """Cut at the first pause once a chunk is min_len long; never exceed
    MAX_CHUNK_S (then cut at the longest pause available, or hard-cut)."""
    cuts, start = [], 0.0
    while True:
        window = [(m, g) for m, g in gaps if start + 1.0 < m <= start + MAX_CHUNK_S]
        if total - start <= MAX_CHUNK_S and not [m for m, _ in window if m - start >= min_len]:
            break
        ready = [m for m, _ in window if m - start >= min_len]
        if ready:
            cut = ready[0]
        elif window:
            cut = max(window, key=lambda x: x[1])[0]
        else:
            cut = start + MAX_CHUNK_S
        if total - cut < min_tail:
            break
        cuts.append(cut)
        start = cut
    bounds = [0.0] + cuts + [total]
    return list(zip(bounds, bounds[1:]))


STYLE = {
    "en": "Hello, this is a clearly punctuated dictation. It uses commas, full stops and question marks where they belong.",
    "fr": "Bonjour, ceci est une dictée bien ponctuée. Elle utilise des virgules, des points et des points d'interrogation là où il faut.",
}


def extract(wav: Path, start: float, end: float, out: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start}", "-to", f"{end}",
         "-i", str(wav), "-c:a", "pcm_s16le", str(out)],
        check=True,
    )
    return out


# --------------------------------------------------------------------- metrics
def labelled_words(text: str):
    """[(normalized word, label)] where label is END, COMMA or ''."""
    out = []
    for raw in text.split():
        word = re.sub(r"[^\w']", "", raw.lower())
        if not word:
            if out and re.search(r"[.?!]", raw):
                out[-1] = (out[-1][0], "END")
            continue
        tail = raw.rstrip("»\"')”’")
        label = "END" if re.search(r"[.?!…]$", tail) else "COMMA" if re.search(r"[,;:]$", tail) else ""
        out.append((word, label))
    return out


def f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def score(reference: str, hypothesis: str, boundary_word_idx):
    ref, hyp = labelled_words(reference), labelled_words(hypothesis)
    sm = difflib.SequenceMatcher(a=[w for w, _ in ref], b=[w for w, _ in hyp], autojunk=False)
    errors = 0
    counts = {"END": [0, 0, 0], "COMMA": [0, 0, 0]}
    hyp_to_ref = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            errors += max(i2 - i1, j2 - j1)
            continue
        for i, j in zip(range(i1, i2), range(j1, j2)):
            hyp_to_ref[j] = i
            r, h = ref[i][1], hyp[j][1]
            for label in counts:
                if h == label and r == label:
                    counts[label][0] += 1
                elif h == label:
                    counts[label][1] += 1
                elif r == label:
                    counts[label][2] += 1
    boundaries = [j for j in boundary_word_idx if j in hyp_to_ref and hyp[j][1] == "END"]
    spurious = [j for j in boundaries if ref[hyp_to_ref[j]][1] != "END"]
    return {
        "wer": errors / len(ref),
        "end_f1": f1(*counts["END"]),
        "comma_f1": f1(*counts["COMMA"]),
        "spurious": (len(spurious) / len(boundary_word_idx)) if boundary_word_idx else 0.0,
    }


# --------------------------------------------------------------------- driver
def run_take(wav: Path, reference: str, language: str, args: dict, variants, tmp: Path):
    prepared = tmp / "prepared.wav"
    prepare_audio(wav, prepared)
    total = duration(prepared)
    style = STYLE.get(language, STYLE["en"])
    results = {}

    for name, prompt in (("whole", ""), ("whole+style", style)):
        text, _, elapsed = transcribe(prepared, args, "auto", prompt)
        results[name] = dict(score(reference, text, []), wait_s=elapsed, total_s=elapsed, chunks=1, text=text)

    gaps = pauses(prepared, args)
    for min_len, mode in variants:
        name = f"chunk{min_len}" + (f"+{mode}" if mode else "")
        texts, times, lang = [], [], "auto"
        cuts = cut_points(gaps, total, min_len)
        for n, (a, b) in enumerate(cuts):
            chunk = extract(prepared, a, b, tmp / f"c{n:03d}.wav")
            tail = " ".join(texts)[-PROMPT_CHARS:]
            prompt = {"": "", "prev": tail, "style": f"{style} {tail}".strip()}[mode]
            t, detected, elapsed = transcribe(chunk, args, lang, prompt)
            if lang == "auto":
                lang = detected
            texts.append(t)
            times.append(elapsed)
        boundary_idx, count = [], 0
        for t in texts[:-1]:
            count += len(labelled_words(t))
            boundary_idx.append(count - 1)
        joined = " ".join(texts)
        results[name] = dict(
            score(reference, joined, boundary_idx),
            wait_s=times[-1], total_s=sum(times), chunks=len(texts),
            cuts=[round(b, 1) for _, b in cuts[:-1]], text=joined,
        )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="first take of each text only")
    parser.add_argument("--out", type=Path, default=REPO / "optimization" / "chunking_results.json")
    opts = parser.parse_args()

    args = base_args()
    variants = [(m, mode) for m in (10, 20) for mode in ("", "prev", "style")]
    takes = sorted(DATASET.glob("*/take_0*.wav"))
    if opts.quick:
        takes = [t for t in takes if t.stem == "take_01"]
    all_results = {}
    for wav in takes:
        reference = (wav.parent / "reference.txt").read_text(encoding="utf-8")
        meta = json.loads((wav.parent / "metadata.json").read_text())
        language = meta.get(wav.stem, {}).get("language", "en")
        with tempfile.TemporaryDirectory() as tmp:
            all_results[f"{wav.parent.name}/{wav.stem}"] = run_take(
                wav, reference, language, args, variants, Path(tmp)
            )
        print(f"done {wav.parent.name}/{wav.stem}", file=sys.stderr, flush=True)
        opts.out.write_text(json.dumps(all_results, indent=1))

    names = list(next(iter(all_results.values())).keys())
    keys = ["wer", "end_f1", "comma_f1", "spurious", "wait_s", "total_s", "chunks"]
    print(f"{'variant':16}" + "".join(f"{k:>10}" for k in keys))
    for name in names:
        rows = [r[name] for r in all_results.values()]
        print(f"{name:16}" + "".join(f"{sum(r[k] for r in rows) / len(rows):10.3f}" for k in keys))


if __name__ == "__main__":
    main()
