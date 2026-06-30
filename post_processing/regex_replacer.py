#!/usr/bin/env python3
import argparse
import os
import sys
import re
import yaml

def build_auto_regex(variations):
    """
    Takes a list of string variations and builds a safe, 
    case-insensitive regex pattern.
    """
    # Sort variations by length descending to prevent partial matches 
    # (e.g., matching "Fim" before "Fim eca")
    variations_sorted = sorted(variations, key=len, reverse=True)
    
    # Escape special characters in the variations (like the dot in bash.rc)
    escaped_vars = [re.escape(v) for v in variations_sorted]
    
    # (?i) makes it case-insensitive
    # \b ensures we only match whole words
    return r'(?i)\b(?:' + '|'.join(escaped_vars) + r')\b'

def load_and_compile(yaml_path):
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    rules = {}
    
    # 1. Process Auto-Generated Rules
    if 'auto_generate' in data:
        for target, variations in data['auto_generate'].items():
            pattern = build_auto_regex(variations)
            rules[pattern] = target
            
    # 2. Process Raw Regex Rules
    if 'raw_regex' in data:
        for pattern, target in data['raw_regex'].items():
            rules[pattern] = target
            
    return rules

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

    replacements = load_and_compile(args.dict)

    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text)

    print("✅ [Regex Replacer] Deterministic substitution complete.")

if __name__ == "__main__":
    main()