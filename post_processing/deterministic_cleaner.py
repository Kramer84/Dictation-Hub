#!/usr/bin/env python3

import json
import re
import sys
import argparse
import os

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
    # Handle varying JSON structures from whisper.cpp
    return data.get('transcription', data.get('segments', []))

def dedup_and_filter_hallucinations(segments):
    """
    Stage 1 & 2: Token-level deduplication and Stretched Artifact Defense.
    """
    cleaned_segments = []
    
    for seg in segments:
        offsets = seg.get('offsets', {})
        start_ms = offsets.get('from', 0)
        end_ms = offsets.get('to', 0)
        duration = end_ms - start_ms

        tokens = seg.get('tokens', [])
        
        # If tokens are missing from the JSON, fallback to splitting the raw text
        if not tokens:
            raw_words = seg.get('text', '').split()
            tokens = [{'text': f" {w}"} for w in raw_words]

        # Stretched Artifact Defense: Drop segments that span long times but contain almost no words
        if duration > MAX_WORD_DURATION_MS and len(tokens) <= 3:
            continue

        cleaned_tokens = []
        streak = 1

        for i in range(len(tokens)):
            curr_text = tokens[i].get('text', '').strip().lower()

            if i > 0:
                prev_text = tokens[i-1].get('text', '').strip().lower()
                if curr_text == prev_text and curr_text != "":
                    streak += 1
                    # Allow 2 backchannels, but strictly 1 content word
                    limit = 2 if curr_text in BACKCHANNEL_WORDS else 1
                    if streak > limit:
                        continue  # Drop consecutive repetition
                else:
                    streak = 1
                    
            cleaned_tokens.append(tokens[i])

        if cleaned_tokens:
            reconstructed_text = "".join([t.get('text', '') for t in cleaned_tokens])
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
    """Identifies short, content-free snippets likely caused by noise."""
    text = text.strip()
    if FILLER_PATTERN.match(text):
        return True
    words = text.split()
    if len(words) <= 3 and len(text) < 25:
        # Keep short sentences that are properly capitalized and punctuated
        if text.endswith(("?", ".")) and text[0].isupper():
            return False
        return True
    return False

def phrase_level_cleanup(entries, gap_threshold_ms=3000):
    """
    Stage 3 - 5: Phrase Level Cleanup (Filler Strip, Fragment Purge, Gap Merge)
    """
    # 3. Pure Filler Strip
    no_fillers = [e for e in entries if not _is_pure_filler(e["text"].strip())]

    # 4. Fragment Purge
    no_fragments = [e for e in no_fillers if not _is_fragment(e["text"])]

    # 5. Gap-Based Merging
    final_out = []
    cur = None
    for e in no_fragments:
        if cur is None:
            cur = dict(e)
        elif (e["start_ms"] - cur["end_ms"]) < gap_threshold_ms:
            # Stitch phrases together if the pause was under the threshold
            cur["end_ms"] = e["end_ms"]
            # Ensure proper spacing when stitching
            text_to_add = e["text"] if e["text"].startswith(" ") else " " + e["text"]
            cur["text"] += text_to_add
        else:
            final_out.append(cur)
            cur = dict(e)
            
    if cur:
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
    args = parser.parse_args()

    if not os.path.exists(args.input_json):
        print(f"Error: {args.input_json} not found.")
        sys.exit(1)

    print(f"-> Running deterministic pre-processing on {args.input_json}...")
    
    # Execution Pipeline
    raw_segments = parse_whisper_json(args.input_json)
    deduped_entries = dedup_and_filter_hallucinations(raw_segments)
    final_cleaned_entries = phrase_level_cleanup(deduped_entries)

    # Output Paths
    base_name = os.path.splitext(args.input_json)[0]
    out_json = f"{base_name}_cleaned.json"
    out_md = f"{base_name}_cleaned.md"

    # Save Sanitized JSON (Retains temporal metadata for the LLM)
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