#!/usr/bin/env python3
import argparse
import os
import sys
import requests
import json
import re
import datetime
import time

class LLMProvider:
    def generate(self, system_prompt, user_text, response_schema=None):
        raise NotImplementedError()

class MistralProvider(LLMProvider):
    def __init__(self, api_key, model):
        self.api_key = api_key
        self.url = "https://api.mistral.ai/v1/chat/completions"
        self.model = model

    def generate(self, system_prompt, user_text, response_schema=None):
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
        
        if response_schema:
            data["response_format"] = {"type": "json_object"}
            
        response = requests.post(self.url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

class LocalProvider(LLMProvider):
    def __init__(self, endpoint, model):
        self.endpoint = endpoint
        self.model = model

    def generate(self, system_prompt, user_text, response_schema=None):
        headers = {"Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.1
        }
        
        if response_schema:
            data["response_format"] = {
                "type": "json_object",
                "json_schema": {
                    "name": "calendar_extraction",
                    "strict": True,
                    "schema": response_schema
                }
            }

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
    parser.add_argument("--schema", default=None)
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

    # 1. Build Base System Instructions
    system_prompt = args.prompt.replace("{language}", args.language)
    
    # 2. Inject Dynamic Temporal Constraints
    now = datetime.datetime.now()
    local_tz = time.tzname[0] if not time.daylight else time.tzname[1]
    temporal_context = (
        f"\n\n=== CURRENT_CONTEXT ===\n"
        f"Current Date: {now.strftime('%Y-%m-%d')}\n"
        f"Current Time: {now.strftime('%H:%M:%S')}\n"
        f"Day of Week: {now.strftime('%A')}\n"
        f"User Timezone: {local_tz}\n"
        f"=======================\n"
    )
    system_prompt += temporal_context

    # 3. Parse and Explicitly Ground the Schema in the System Prompt Text
    response_schema = None
    if args.schema:
        try:
            response_schema = json.loads(args.schema)
            schema_grounding = (
                f"\n\n=== MANDATORY JSON OUTPUT SCHEMA ===\n"
                f"You MUST output a JSON object that strictly adheres to this JSON schema structure:\n"
                f"{json.dumps(response_schema, indent=2)}\n"
                f"Do not include markdown wrappers, thoughts, or extra fields outside this schema layout.\n"
                f"====================================\n"
            )
            system_prompt += schema_grounding
        except Exception as json_err:
            print(f"⚠️ [LLM Runner] Failed parsing schema argument block: {json_err}")

    try:
        if args.provider.lower() == "mistral":
            api_key = os.environ.get("MISTRAL_API_KEY")
            if not api_key:
                raise ValueError("MISTRAL_API_KEY is missing.")
            provider = MistralProvider(api_key, args.model)
        else:
            provider = LocalProvider(args.endpoint, args.model)
            
        result_text = provider.generate(system_prompt, user_text, response_schema)
        
        if not response_schema:
            result_text = re.sub(r'\s*\[\?+\]|\s*\[[-+]+\]', '', result_text)
            
    except Exception as e:
        print(f"❌ [LLM Runner] Execution Failed: {e}")
        result_text = user_text if not response_schema else "{}"

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result_text)
    
    print(f"✅ [LLM Runner] Completed step using {args.provider}/{args.model}")

if __name__ == "__main__":
    main()