import os
import sys
import json
import argparse
import time
import requests
import string
import re
import my_mistral

# Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
THEMES_FILE = os.path.expanduser("~/.whisper_transcriptions/themes.txt")
DEFAULT_THEMES = ["Prompt", "Command", "Explanation", "Question", "Journal", "Other"]

def load_or_create_themes():
    if not os.path.exists(THEMES_FILE):
        os.makedirs(os.path.dirname(THEMES_FILE), exist_ok=True)
        with open(THEMES_FILE, "w") as f:
            f.write("\n".join(DEFAULT_THEMES))
        return DEFAULT_THEMES
    with open(THEMES_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines

def json_to_marked_text(json_path):
    """
    Reconstructs text from Whisper JSON with confidence markers.
    """
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"      [!] Error reading JSON {json_path}: {e}")
        return ""

    out_text = []

    # Handle both structure types (Whisper.cpp sometimes varies)
    segments = data.get('transcription', data.get('segments', []))

    for segment in segments:
        # Some versions put tokens inside 'tokens', others flat
        tokens = segment.get('tokens', [])

        for token in tokens:
            # Handle object vs string tokens
            if isinstance(token, dict):
                text = token.get('text', '')
                p = token.get('p', 1.0) # probability
            else:
                # Fallback if raw text
                text = str(token)
                p = 1.0

            # Marker Logic (User's Preference)
            if p < 0.4:
                text += "[---]"
            elif p < 0.6:
                text += "[--]"
            elif p < 0.8:
                text += "[-]"
            out_text.append(text)

    return "".join(out_text)

def compress_repetitions_marked(text, min_phrase_len=2, max_phrase_len=60):
    """
    Scans for repeating phrases and collapses them into a marker [Rx].
    - High performance (pre-computed cleanup).
    - Ignores [_EOT_] and confidence markers during comparison.
    - Format: "Phrase [R3]" (Total 3 occurrences: 1 kept + 2 removed).
    """
    if not text: return ""

    tokens = text.split()
    n = len(tokens)

    # 1. CLEANING HELPER (From Function 2)
    # We define it here to handle specific artifacts like [_EOT_]
    def clean_token(t):
        # Remove confidence markers/diffs like [---], [+++], [0.98]
        t_base = re.sub(r'\[[-+_.\d]+\]', '', t)
        # Remove specific [_EOT_] marker
        t_base = re.sub(r'\[_EOT_\]', '', t_base)
        t_base = re.sub(r'\[_TT_\d+\]|\[_BEG_\]', '', t_base)
        # Standardize
        return t_base.lower().strip(string.punctuation)

    # 2. PRE-COMPUTATION (From Function 1 - Performance Optimization)
    # We clean the whole list once, avoiding repetitive regex calls in the loop
    cleaned_tokens = [clean_token(t) for t in tokens]

    output_tokens = []
    i = 0

    while i < n:
        best_len = 0
        best_count = 0

        # Greedy search from max_phrase_len down to min
        for L in range(max_phrase_len, min_phrase_len - 1, -1):
            if i + 2*L > n: continue

            # Compare using the pre-cleaned list
            pat = cleaned_tokens[i : i+L]
            nxt = cleaned_tokens[i+L : i+2*L]

            if pat == nxt:
                # We found a repetition. Now count how many times it loops.
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
                break # Stop searching for shorter phrases, we found the longest

        if best_len > 0:
            # Add the phrase ONCE (preserving original formatting of the first instance)
            output_tokens.extend(tokens[i : i+best_len])

            # Add the requested Short Marker
            # Note: best_count is the TOTAL occurrences.
            # If you want only the *removed* count, change to: best_count - 1
            output_tokens.append(f" [R{best_count}] ")

            # Skip over all instances
            i += best_len * best_count
        else:
            output_tokens.append(tokens[i])
            i += 1

    return " ".join(output_tokens)

def call_ollama_json(prompt, model="llama3.2"):
    try:
        start_t = time.time()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 8192, "temperature": 0.2}
        }
        response = requests.post(OLLAMA_URL, json=payload, timeout=300)
        response.raise_for_status()
        duration = time.time() - start_t
        return response.json().get("response", ""), duration
    except Exception as e:
        print(f"      [!] Ollama Error: {e}")
        return None, 0

def process_intermediary(file_path, themes):
    # 1. READ JSON & RECONSTRUCT WITH MARKERS
    raw_text_marked = json_to_marked_text(file_path)

    if not raw_text_marked:
        # Fallback to reading the txt file if JSON fails?
        # Actually, let's just fail specific to this version
        return "", "Unknown", "Unknown", 0

    # 2. HEAVY COMPRESSION (The "R20" Logic)
    compressed_text = compress_repetitions_marked(raw_text_marked)

    # Save the compressed/marked intermediate for debugging
    base_dir = os.path.dirname(file_path)
    fname = os.path.splitext(os.path.basename(file_path))[0]
    with open(os.path.join(base_dir, f"{fname}_compressed.txt"), "w") as f:
        f.write(compressed_text)

    # 3. Llama Prompt
    theme_list_str = json.dumps(themes)

    prompt = f"""
    You are a data cleaning assistant.
    Input Text (contains confidence markers like [---] and repetition markers):
    "{compressed_text}"

    Tasks:
    1. "content": Clean the text.
       - Remove low confidence content (marked [---]) ONLY if it makes no sense.
       - Remove repetition markers (e.g. "[R20]" if repeated 20 times) and the repeated phrase itself if it is hallucination/garbage.
       - Keep valid technical details.
    2. "theme": Select best fit from: {theme_list_str}.
    3. "descriptor": Short underscore_descriptor.

    Output STRICT JSON:
    {{
      "theme": "SelectedTheme",
      "descriptor": "short_descriptor",
      "content": "Cleaned text..."
    }}
    """

    print(f"   -> Processing {os.path.basename(file_path)} (JSON Mode)...")
    output_str, duration = call_ollama_json(prompt)

    parsed_theme = "Unknown"
    parsed_desc = "Unknown"
    parsed_content = compressed_text # Fallback to compressed

    if output_str:
        try:
            data = json.loads(output_str)
            parsed_theme = data.get("theme", "Unknown")
            parsed_desc = data.get("descriptor", "Unknown")
            parsed_content = data.get("content", compressed_text)
        except json.JSONDecodeError:
            print(f"      [!] JSON Decode Error.")

    return parsed_content, parsed_theme, parsed_desc, duration

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs='+', required=True) # Now expects JSON files
    parser.add_argument("--target", required=True)
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()

    themes = load_or_create_themes()
    versions_data = []

    print(f"-> AI Processing chain started (JSON Source).")

    theme_votes = {}

    for fpath in args.files:
        if not os.path.exists(fpath): continue

        cleaned_text, theme, desc, dur = process_intermediary(fpath, themes)

        # Store source name without extension
        src_name = os.path.splitext(os.path.basename(fpath))[0]

        versions_data.append({
            "source": src_name,
            "text": cleaned_text,
            "theme": theme,
            "desc": desc
        })

        print(f"      [Llama] {src_name}: {dur:.2f}s | Theme: {theme} | Desc: {desc}")

        if theme != "Unknown":
            theme_votes[theme] = theme_votes.get(theme, 0) + 1

    if theme_votes:
        best_theme = max(theme_votes, key=theme_votes.get)
    else:
        best_theme = "Unknown"

    valid_descs = [v['desc'] for v in versions_data if v['desc'] != "Unknown"]
    best_desc = max(valid_descs, key=len) if valid_descs else "transcription"

    if best_theme != "Unknown" and best_theme not in themes and len(best_theme) < 25:
        with open(THEMES_FILE, "a") as f:
            f.write(f"\n{best_theme}")

    # Mistral Consensus
    combined_prompt = f"""
    I have {len(versions_data)} transcription drafts.
    Metadata: [Theme: {best_theme}, Descriptor: {best_desc}].

    Merge them into one Final, Accurate Transcription.
    Output ONLY the final text.
    """

    for i, v in enumerate(versions_data):
        combined_prompt += f"\n\n--- DRAFT {i+1} ({v['source']}) ---\n{v['text']}"

    print(f"-> Finalizing with Mistral...")
    try:
        mm = my_mistral.MistralAgentHandler.MistralAgentHandler(agent_id=args.agent)
        response = mm.get_agent_response(user_input=combined_prompt)
        final_text = response.choices[0].message.content
    except Exception as e:
        print(f"   [Error] Mistral failed: {e}")
        final_text = versions_data[0]['text']

    with open(args.target, "w") as f:
        f.write(f"Theme: {best_theme}\nDescriptor: {best_desc}\n\n")
        f.write(final_text)
        f.write(f"\n\n---\n*Auto-generated. Theme: {best_theme} | Desc: {best_desc}*")

    meta_path = os.path.join(os.path.dirname(args.target), "final_metadata.json")
    with open(meta_path, "w") as f:
        json.dump({"theme": best_theme, "descriptor": best_desc}, f)

if __name__ == "__main__":
    main()
