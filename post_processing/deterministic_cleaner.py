#!/usr/bin/env python3

import json
import re
import sys
import argparse
import os
import string

# --- Constants Ported from aside.py ---
BACKCHANNEL_WORDS = {
    "yeah", "yeah.", "mm-hmm", "mm-hmm.", "mhm", "mhm.", "mm", "hmm",
    "wow", "wow.", "nice", "nice.", "sure", "sure.", "right", "right.",
    "-hmm", "-hmm.", "uh-huh", "uh-huh."
}

PURE_FILLER_RE = re.compile(
    r"^(Mm-hmm|Mhm|Mm|Hmm|-hmm|Yeah|Yep|Wow|Nice|Sure|Right|Okay|Cool|Oh|Uh-huh)[.\s,!?]*$",
    re.IGNORECASE
)

FILLER_PATTERN = re.compile(
    r"^[\s.,!?]*(mm[-\s]?hmm|mhm|mm|hmm|yeah|yep|wow|nice|sure|right|okay|oh|uh[-\s]?huh|ah|um|i|so)([.\s,!?]|\s)*$",
    re.IGNORECASE
)

MAX_WORD_DURATION_MS = 5000  # Cutoff for stretched hallucination artifacts

def parse_whisper_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('transcription', data.get('segments', []))

def add_confidence_marker(text, p_value):
    """Applies legacy confidence tracking markers to the token text."""
    if p_value < 0.4:
        return f"{text}[---]"
    elif p_value < 0.6:
        return f"{text}[--]"
    elif p_value < 0.8:
        return f"{text}[-]"
    elif p_value > 0.99:
        return f"{text}[+]"
    return text

def compress_repetitions_marked(text, min_phrase_len=2, max_phrase_len=60):
    """
    Scans for repeating phrases and collapses them into a marker [Rx].
    """
    if not text: 
        return ""

    tokens = text.split()
    n = len(tokens)

    def clean_token(t):
        t_base = re.sub(r'\[[-+_.\d]+\]', '', t)
        t_base = re.sub(r'\[_EOT_\]', '', t_base)
        t_base = re.sub(r'\[_TT_\d+\]|\[_BEG_\]', '', t_base)
        return t_base.lower().strip(string.punctuation)

    cleaned_tokens = [clean_token(t) for t in tokens]
    output_tokens = []
    i = 0

    while i < n:
        best_len = 0
        best_count = 0

        for L in range(max_phrase_len, min_phrase_len - 1, -1):
            if i + 2*L > n: 
                continue

            pat = cleaned_tokens[i : i+L]
            nxt = cleaned_tokens[i+L : i+2*L]

            if pat == nxt:
                count = 1
                curr_idx = i + L
                while curr_idx + L <= n:
                    if cleaned_tokens[curr_idx : curr_idx+L] == pat:
                        count += 1
                        curr_idx += L
                    else:
                        break
                best_len = L
                best_count = count
                break 

        if best_len > 0:
            output_tokens.extend(tokens[i : i+best_len])
            output_tokens.append(f" [R{best_count}] ")
            i += best_len * best_count
        else:
            output_tokens.append(tokens[i])
            i += 1

    return " ".join(output_tokens)

def dedup_and_filter_hallucinations(segments, mark_confidence=False):
    """
    Stage 1 & 2: Token-level deduplication and Stretched Artifact Defense.
    """
    cleaned_segments = []
    
    for seg in segments:
        offsets = seg.get('offsets', {})
        start_ms = offsets.get('from', seg.get('start', 0) * 1000 if 'start' in seg else 0)
        end_ms = offsets.get('to', seg.get('end', 0) * 1000 if 'end' in seg else 0)
        
        # Guard against malformed JSON from whisper implementations
        if isinstance(start_ms, float): start_ms = int(start_ms)
        if isinstance(end_ms, float): end_ms = int(end_ms)
        
        duration = end_ms - start_ms
        tokens = seg.get('tokens', [])
        
        if not tokens:
            raw_words = seg.get('text', '').split()
            tokens = [{'text': f" {w}", 'p': 1.0} for w in raw_words]

        if duration > MAX_WORD_DURATION_MS and len(tokens) <= 3:
            continue

        cleaned_tokens = []
        streak = 1

        for i in range(len(tokens)):
            # Handle mixed token structures
            token_obj = tokens[i]
            if isinstance(token_obj, dict):
                raw_text = token_obj.get('text', '')
                p_val = token_obj.get('p', 1.0)
            else:
                raw_text = str(token_obj)
                p_val = 1.0

            curr_text = raw_text.strip().lower()

            if i > 0:
                prev_text_raw = tokens[i-1].get('text', '') if isinstance(tokens[i-1], dict) else str(tokens[i-1])
                prev_text = prev_text_raw.strip().lower()
                if curr_text == prev_text and curr_text != "":
                    streak += 1
                    limit = 2 if curr_text in BACKCHANNEL_WORDS else 1
                    if streak > limit:
                        continue 
                else:
                    streak = 1
            
            # Apply confidence marker if flag is present
            final_text = raw_text
            if mark_confidence:
                final_text = add_confidence_marker(raw_text, p_val)

            cleaned_tokens.append(final_text)

        if cleaned_tokens:
            reconstructed_text = "".join(cleaned_tokens)
            cleaned_segments.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": reconstructed_text
            })

    return cleaned_segments

def _is_pure_filler(text):
    stripped = PURE_FILLER_RE.sub("", text).strip()
    return not stripped

def _is_fragment(text):
    text = text.strip()
    if FILLER_PATTERN.match(text):
        return True
    words = text.split()
    if len(words) <= 3 and len(text) < 25:
        if text.endswith(("?", ".")) and text[0].isupper():
            return False
        return True
    return False

def phrase_level_cleanup(entries, gap_threshold_ms=3000, apply_compression=False):
    """
    Stage 3 - 5: Phrase Level Cleanup (Filler Strip, Fragment Purge, Gap Merge)
    """
    no_fillers = [e for e in entries if not _is_pure_filler(e["text"].strip())]
    no_fragments = [e for e in no_fillers if not _is_fragment(e["text"])]

    final_out = []
    cur = None
    for e in no_fragments:
        if cur is None:
            cur = dict(e)
        elif (e["start_ms"] - cur["end_ms"]) < gap_threshold_ms:
            cur["end_ms"] = e["end_ms"]
            text_to_add = e["text"] if e["text"].startswith(" ") else " " + e["text"]
            cur["text"] += text_to_add
        else:
            if apply_compression:
                cur["text"] = compress_repetitions_marked(cur["text"])
            final_out.append(cur)
            cur = dict(e)
            
    if cur:
        if apply_compression:
            cur["text"] = compress_repetitions_marked(cur["text"])
        final_out.append(cur)

    return final_out

def format_timestamp(ms):
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"

def main():
    parser = argparse.ArgumentParser(description="Deterministic pre-processor for Whisper JSON.")
    parser.add_argument("input_json", help="Path to the whisper _full.json file")
    parser.add_argument("--mark-confidence", action="store_true", help="Append [---] markers based on p value")
    parser.add_argument("--compress-repetitions", action="store_true", help="Collapse looped phrases into [Rx] markers")
    args = parser.parse_args()

    if not os.path.exists(args.input_json):
        print(f"Error: {args.input_json} not found.")
        sys.exit(1)

    print(f"-> Running deterministic pre-processing on {args.input_json}...")
    
    raw_segments = parse_whisper_json(args.input_json)
    
    # Execution Pipeline
    deduped_entries = dedup_and_filter_hallucinations(raw_segments, mark_confidence=args.mark_confidence)
    final_cleaned_entries = phrase_level_cleanup(deduped_entries, apply_compression=args.compress_repetitions)

    # Output Paths
    base_name = os.path.splitext(args.input_json)[0]
    out_json = f"{base_name}_cleaned.json"
    out_md = f"{base_name}_cleaned.md"

    # Save Sanitized JSON
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({"segments": final_cleaned_entries}, f, indent=2)

    # Save Human-Readable Markdown
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write("# Cleaned Transcription\n\n")
        for e in final_cleaned_entries:
            ts = format_timestamp(e['start_ms'])
            f.write(f"**{ts}** {e['text'].strip()}\n\n")

    print(f"✅ Scrubbed output saved to {out_json} and {out_md}")

if __name__ == "__main__":
    main()