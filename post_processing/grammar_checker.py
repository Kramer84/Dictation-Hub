#!/usr/bin/env python3
import argparse
import os
import sys
import re
import language_tool_python

LANG_MAP = {
    "en": "en-US",
    "fr": "fr-FR",
    "de": "de-DE",
}

def main():
    parser = argparse.ArgumentParser(description="Deterministic Grammar Checker")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    # MANDATORY DEFENSE: Grammar checkers cannot parse confidence metadata.
    text = re.sub(r'\s*\[\?+\]|\s*\[[-+]+\]', '', text)

    lt_lang = LANG_MAP.get(args.language, "en-US")
    tool = language_tool_python.LanguageToolPublicAPI(lt_lang)
    
    corrected_text = tool.correct(text)
    tool.close()

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(corrected_text)

    print(f"✅ [Grammar Checker] Truecasing and punctuation restored (Lang: {lt_lang}).")

if __name__ == "__main__":
    main()