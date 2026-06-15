import os
import sys
import json
import re
import string
import difflib
import my_mistral

# --- CONFIGURATION ---
DISCLAIMER_MSG = "\n\n---\n*Disclaimer: This text was transcribed automatically and refined by an AI. Please verify critical details for accuracy.*"

def modify_filename(path, suffix):
    directory = os.path.dirname(path)
    base = os.path.basename(path)
    name, ext = os.path.splitext(base)
    # Avoid double suffixes if re-running
    if name.endswith(suffix):
        return path
    new_path = os.path.join(directory, name + suffix + ext)
    return new_path

def add_marker(token, p_value):
    if p_value < 0.4:
        return f"{token}[---]"
    elif p_value < 0.6:
        return f"{token}[--]"
    elif p_value < 0.8:
        return f"{token}[-]"
    elif p_value > 0.99:
        return f"{token}[+]"
    else:
        return f"{token}"

def reconstruct_text(json_file):
    with open(json_file, 'r') as f:
        data = json.load(f)

    reconstructed_text = ""
    try:
        language = data["result"]["language"]
        reconstructed_text += f"- Language: {language} -.\n"
    except KeyError:
        pass

    for segment in data['transcription']:
        for token in segment['tokens']:
            token_text = token['text']
            p_value = token.get('p', 0)
            marker_added_token = add_marker(token_text, p_value)
            if marker_added_token:
                reconstructed_text += marker_added_token

    return reconstructed_text

def strip_markers(text):
    """Removes markers to create a raw baseline."""
    clean = re.sub(r'\[[-+_]+\]', '', text)
    clean = re.sub(r'\[_EOT_\]', '', clean)
    return clean

def verify_integrity(original_text, cleaned_text, threshold=0.5): # Lowered threshold slightly for strict check
    """
    Compares the raw original text (minus markers) with the LLM output.
    Returns True if the content overlap is high enough.
    """
    raw_input = strip_markers(original_text)

    # NORMALIZE WHITESPACE:
    # Split by any whitespace (spaces, tabs, newlines) and rejoin with single spaces.
    # This ensures "Hello\nWorld" matches "Hello World" perfectly.
    raw_flat = " ".join(raw_input.split())
    clean_flat = " ".join(cleaned_text.split())

    # Calculate similarity ratio on flattened text
    matcher = difflib.SequenceMatcher(None, raw_flat, clean_flat)
    similarity = matcher.ratio()

    # Calculate length ratio based on character count (ignoring whitespace differences)
    len_ratio = len(clean_flat) / len(raw_flat) if len(raw_flat) > 0 else 0

    if similarity < threshold:
        print(f"[Warning] Integrity check failed (Similarity too low).")
        return False

    if len_ratio < 0.5:
        print(f"[Warning] Integrity check failed (Text too short).")
        return False

    return True

def llm_postprocessor(text, agent_id=None):
    # --- STEP 1: Algorithmic Cleaning (N-Gram Filter) ---
    # Remove "You. You. You." and "Water bottle. Water bottle." loops
    print("[Pre-Process] Running N-Gram Repetition Filter...")
    text = remove_repetitive_segments(text)

    if agent_id is None:
        try:
            agent_id = os.environ["MISTRAL_AGENT_ID_TRANSCRIPTION"]
        except KeyError:
            print("[Error] MISTRAL_AGENT_ID_TRANSCRIPTION not found in env.")
            return strip_markers(text)

    try:
        mm = my_mistral.MistralAgentHandler.MistralAgentHandler(agent_id=agent_id)
        response = mm.get_agent_response(user_input=text)

        if hasattr(response, 'choices') and response.choices:
            raw_llm_output = response.choices[0].message.content

            # --- FORCE CLEANUP ---
            # Even if LLM ignores instructions, we remove the markers programmatically
            cleaned_output = strip_markers(raw_llm_output)

            # --- INTEGRITY CHECK ---
            if verify_integrity(text, cleaned_output):
                return cleaned_output
            else:
                print("LLM output deviated too much. Returning cleaned output anyway.")
                return cleaned_output
        else:
            raise ValueError("Mistral Response contained no choices.")

    except Exception as e:
        print(f"Failed to clean output using Mistral AI: {e}")
        return strip_markers(text)


def remove_repetitive_segments(text, min_phrase_len=2, max_phrase_len=60):
    """
    Scans for repeating n-grams (phrases) and removes consecutive duplicates.
    Now insensitive to punctuation and case during comparison.
    """
    if not text:
        return ""

    tokens = text.split()
    n = len(tokens)

    # Helper to clean tokens for comparison
    def get_clean_window(token_slice):
        cleaned = []
        for t in token_slice:
            # 1. Remove the specific confidence markers first
            t_no_marker = re.sub(r'\[[-+_]+\]', '', t)
            t_no_marker = re.sub(r'\[_EOT_\]', '', t_no_marker)

            # 2. Then strip standard punctuation and lowercase
            clean_t = t_no_marker.strip(string.punctuation).lower()
            cleaned.append(clean_t)
        return cleaned

    cleaned_tokens = []
    i = 0

    while i < n:
        best_match_len = 0

        # Check for repetitions of length L
        # Increased max_phrase_len to 60 to catch longer looping paragraphs (A B A B)
        for L in range(max_phrase_len, min_phrase_len - 1, -1):
            if i + 2 * L > n:
                continue

            # Compare the CLEANED versions of the windows
            clean_window_1 = get_clean_window(tokens[i : i + L])
            clean_window_2 = get_clean_window(tokens[i + L : i + 2 * L])

            if clean_window_1 == clean_window_2:
                best_match_len = L
                break

        if best_match_len > 0:
            # Found a repetition. Add the phrase ONCE (preserving original formatting)
            cleaned_tokens.extend(tokens[i : i + best_match_len])

            # Skip over the immediate duplicate(s)
            i += best_match_len

            # Keep checking forward for MORE repetitions of the same phrase
            while i + best_match_len <= n:
                next_segment_clean = get_clean_window(tokens[i : i + best_match_len])
                prev_segment_clean = get_clean_window(tokens[i - best_match_len : i])

                if next_segment_clean == prev_segment_clean:
                    i += best_match_len
                else:
                    break
        else:
            # No repetition, keep current token
            cleaned_tokens.append(tokens[i])
            i += 1

    return " ".join(cleaned_tokens)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python reconstruct_transcription.py <input_file> <output_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] # This is the main file the bash script will display

    print(f"Reconstructing text from {input_file}...")
    reconstructed_text = reconstruct_text(input_file)

    print("Running LLM post-processing...")
    reconstructed_text_llm = llm_postprocessor(reconstructed_text)

    # Save the RAW (marked) text to a separate debug file
    debug_file = modify_filename(output_file, "_raw_marked")

    # Save the CLEANED text to the main output file
    with open(debug_file, "w+") as f_debug:
        f_debug.write(reconstructed_text)

    # Save the final cleaned text WITH THE DISCLAIMER
    with open(output_file, "w+") as f_main:
        f_main.write(reconstructed_text_llm + DISCLAIMER_MSG)

    print("Transcription cleaning completed.")
    print(f"Cleaned Output: {output_file}")
    print(f"Debug (Raw):    {debug_file}")
