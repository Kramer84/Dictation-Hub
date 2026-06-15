import os
import sys
import json
import argparse
import requests
import my_mistral  # Using your existing library

def call_ollama(text, model="llama3"):
    """
    Calls a local Ollama instance to clean the text.
    Assumes Ollama is running on localhost:11434.
    """
    prompt = f"""
    You are a transcription cleaner.
    Below is a raw transcription of audio. It may contain loops, hallucinations, or stuttering.
    Please output ONLY the corrected text. Do not summarize. Keep all technical details.

    Raw Text:
    {text}
    """

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120
        )
        response.raise_for_status()
        return response.json().get("response", text)
    except Exception as e:
        print(f"[Warn] Ollama call failed ({e}). Returning original.")
        return text

def merge_and_finalize(files, output_file, agent_id):
    """
    1. Loads multiple transcription versions.
    2. Cleans each locally (Ollama).
    3. Sends all versions to Mistral to pick/merge the best one.
    """
    versions = []

    print(f"-> Loading {len(files)} versions...")
    for i, fpath in enumerate(files):
        if not os.path.exists(fpath):
            continue

        with open(fpath, 'r') as f:
            raw_content = f.read()

        print(f"   - Cleaning version {i+1} with local Ollama...")
        clean_content = call_ollama(raw_content)
        versions.append(f"--- VERSION {i+1} ---\n{clean_content}\n")

    combined_input = "\n".join(versions)

    final_prompt = (
        "Here are multiple transcription attempts of the same audio using different settings. "
        "Compare them, ignore hallucinations (like repeating loops), and reconstruct the most accurate final text. "
        "Output ONLY the final text."
        f"\n\n{combined_input}"
    )

    print("-> Finalizing with Mistral Agent...")
    try:
        mm = my_mistral.MistralAgentHandler.MistralAgentHandler(agent_id=agent_id)
        response = mm.get_agent_response(user_input=final_prompt)

        if hasattr(response, 'choices') and response.choices:
            final_text = response.choices[0].message.content
        else:
            final_text = versions[0] # Fallback

        # Write Output
        with open(output_file, 'w') as out:
            out.write(final_text)
            out.write("\n\n---\n*Merged from multiple sources and refined by AI.*")

        print(f"-> Success! Saved to {output_file}")

    except Exception as e:
        print(f"[Error] Mistral Finalization failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", nargs='+', required=True, help="List of txt files to merge")
    parser.add_argument("--target", required=True, help="Final output file")
    parser.add_argument("--agent", required=True, help="Mistral Agent ID")
    args = parser.parse_args()

    merge_and_finalize(args.outputs, args.target, args.agent)
