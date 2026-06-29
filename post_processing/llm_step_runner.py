#!/usr/bin/env python3
import argparse
import os
import sys
import requests
import json

class LLMProvider:
    def generate(self, system_prompt, user_text, enforce_json=False):
        raise NotImplementedError()

class MistralProvider(LLMProvider):
    def __init__(self, api_key, model):
        self.api_key = api_key
        self.url = "https://api.mistral.ai/v1/chat/completions"
        self.model = model

    def generate(self, system_prompt, user_text, enforce_json=False):
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
            "temperature": 0.1
        }
        if enforce_json:
            data["response_format"] = {"type": "json_object"}
            
        response = requests.post(self.url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

class LocalProvider(LLMProvider):
    def __init__(self, endpoint, model):
        self.endpoint = endpoint
        self.model = model

    def generate(self, system_prompt, user_text, enforce_json=False):
        headers = {"Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.1
        }
        if enforce_json:
            data["response_format"] = {"type": "json_object"}

        response = requests.post(self.endpoint, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

def main():
    parser = argparse.ArgumentParser(description="Agnostic LLM Step Runner")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", required=True, help="mistral or local")
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="http://localhost:11434/v1/chat/completions")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--enforce-json", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        user_text = f.read().strip()

    if not user_text:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("")
        return

    system_prompt = args.prompt.replace("{lang}", args.language)
    
    try:
        if args.provider.lower() == "mistral":
            api_key = os.environ.get("MISTRAL_API_KEY")
            if not api_key:
                raise ValueError("MISTRAL_API_KEY is missing.")
            provider = MistralProvider(api_key, args.model)
        else:
            provider = LocalProvider(args.endpoint, args.model)
            
        result_text = provider.generate(system_prompt, user_text, args.enforce_json)
        
        # Cleanup artifacts
        result_text = result_text.replace("[---]", "").replace("[--]", "").replace("[-]", "").replace("[+]", "")
    except Exception as e:
        print(f"❌ [LLM Runner] Execution Failed: {e}")
        result_text = user_text if not args.enforce_json else "{}"

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result_text)
    
    print(f"✅ [LLM Runner] Completed step using {args.provider}/{args.model}")

if __name__ == "__main__":
    main()