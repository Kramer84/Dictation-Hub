#!/usr/bin/env python3
import argparse
import os
import sys
import json
import re

def main():
    parser = argparse.ArgumentParser(description="Deterministic Regex Replacer")
    parser.add_argument("--input", required=True, help="Path to raw text")
    parser.add_argument("--output", required=True, help="Path to output text")
    parser.add_argument("--dict", required=True, help="Path to the JSON dictionary of regex rules")
    parser.add_argument("--strip-markers", action="store_true", help="Remove all Whisper confidence markers before processing")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    # Optional: Wipe markers if moving into a purely deterministic, non-LLM pipeline
    if args.strip_markers:
        # Catches both your current [?] style and previous [-] style markers
        text = re.sub(r'\s*\[\?+\]|\s*\[[-+]+\]', '', text)

    with open(args.dict, "r", encoding="utf-8") as f:
        replacements = json.load(f)

    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text)

    print("✅ [Regex Replacer] Deterministic substitution complete.")

if __name__ == "__main__":
    main()