#!/usr/bin/env python3
import yaml
import re

# ANSI Color Codes for terminal highlighting
GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

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

def test_string(text, rules):
    print("--- REGEX TESTER ---")
    print(f"RAW TEXT: {text}\n")
    
    final_text = text
    for pattern, replacement in rules.items():
        # Find all matches before replacing to show the user what triggered
        matches = re.findall(pattern, final_text)
        if matches:
            for match in set(matches):
                print(f"[{GREEN}MATCH{RESET}] Regex caught '{RED}{match}{RESET}' -> Correcting to '{GREEN}{replacement}{RESET}'")
                print(f"       (Using generated pattern: {pattern})")
            
            # Apply the replacement
            final_text = re.sub(pattern, replacement, final_text)
            
    print(f"\nFINAL TEXT: {final_text}")

if __name__ == "__main__":
    # Test Data from your latest dictations
    test_dictation = (
        "I have some experience with Scython. I've often worked with "
        "OpenTurns which is developed by Phineka. My PhD director is "
        "Nicolas Guéton. I had bash.rc which is a Linux file."
    )
    
    # Assuming the YAML is saved as hallucinations_dict.yaml in the same dir
    try:
        rules = load_and_compile('configs/hallucinations_dict.yaml')
        test_string(test_dictation, rules)
    except FileNotFoundError:
        print("Please create hallucinations_dict.yaml first.")