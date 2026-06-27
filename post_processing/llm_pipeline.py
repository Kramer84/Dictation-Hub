#!/usr/bin/env python3
import argparse
import os
import sys
import requests

class LLMProvider:
    def generate(self, system_prompt, user_text):
        raise NotImplementedError("Subclasses must implement this method")

class MistralProvider(LLMProvider):
    def __init__(self, api_key):
        self.api_key = api_key
        self.url = "https://api.mistral.ai/v1/chat/completions"
        self.model = "mistral-large-latest"

    def generate(self, system_prompt, user_text):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.2
        }
        response = requests.post(self.url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

class LocalProvider(LLMProvider):
    # Compatible with any OpenAI-spec local server (Ollama, LM Studio, Text-Generation-WebUI)
    def __init__(self, endpoint, model):
        self.endpoint = endpoint
        self.model = model

    def generate(self, system_prompt, user_text):
        headers = {"Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.2
        }
        response = requests.post(self.endpoint, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

# --- Prompt Routing Dictionary ---
PROMPTS = {
    "mail": "You are an assistant. Format the following dictated text into a professional, clear, and concise email. Do not add conversational filler or acknowledge the prompt. Output only the email content.",
    "notes": "You are an assistant. Extract the key action items, decisions, and main points from the following dictated text. Format them as concise, highly readable bullet points.",
    "standard": "You are a text cleaner. Fix only the punctuation, capitalization, and obvious transcription misinterpretations in the following text. Do not rewrite the meaning, style, or remove filler words."
}

def get_provider():
    # Dynamic routing based on environment configuration injected by the pipeline
    provider_type = os.environ.get("LLM_PROVIDER", "local").lower()
    
    if provider_type == "mistral":
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY environment variable is missing.")
        print("[LLM Pipeline] Routing inference to Mistral API")
        return MistralProvider(api_key)
        
    else:
        # Default to standard local port used by Ollama. 
        # LM Studio defaults to 1234, vLLM to 8000.
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434/v1/chat/completions")
        local_model = os.environ.get("LOCAL_LLM_MODEL", "llama3")
        print(f"[LLM Pipeline] Routing inference to Local Server ({local_url})")
        return LocalProvider(local_url, local_model)

def main():
    parser = argparse.ArgumentParser(description="LLM Post-Processing Pipeline")
    parser.add_argument("--input", required=True, help="Path to the raw text input file")
    parser.add_argument("--output", required=True, help="Path to save the generated text")
    parser.add_argument("--mode", required=True, help="Dictates the system prompt (e.g., mail, notes)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        user_text = f.read().strip()

    # Bypass execution if transcription is entirely empty
    if not user_text:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("")
        return

    system_prompt = PROMPTS.get(args.mode, PROMPTS["standard"])
    
    try:
        provider = get_provider()
        result_text = provider.generate(system_prompt, user_text)
    except Exception as e:
        print(f"❌ [LLM Pipeline] Execution Failed: {e}")
        print("-> Bypassing LLM step and returning raw text as fallback.")
        result_text = user_text

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result_text)
    
    print(f"✅ [LLM Pipeline] Completed ({args.mode} mode)")

if __name__ == "__main__":
    main()