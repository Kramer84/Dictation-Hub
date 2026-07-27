#!/usr/bin/env python3
import argparse
import os
import json
import glob
import requests
import sys

# Paths
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
CONFIG_JSON_PATH = os.path.join(REPO_ROOT, "configs", "pipeline_config.json")

def load_pipeline_config():
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def push_to_n8n(webhook_url, payload, workspace_name):
    """Sends the payload to the dynamically assigned n8n webhook."""
    try:
        # A short timeout ensures the main pipeline doesn't hang if n8n is offline
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        print(f"[Dispatcher] ✅ Successfully pushed {workspace_name} to n8n.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[Dispatcher] ⚠️ Failed to reach n8n for {workspace_name}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Synchronous n8n Webhook Dispatcher")
    parser.add_argument("--workspace", required=True, help="Path to the completed workspace")
    args = parser.parse_args()

    workspace_dir = args.workspace
    workspace_name = os.path.basename(workspace_dir)

    # 1. Read Metadata
    meta_path = os.path.join(workspace_dir, "metadata.json")
    if not os.path.exists(meta_path):
        print(f"[Dispatcher] ⚠️ No metadata.json found in {workspace_dir}. Aborting.")
        sys.exit(1)
        
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
        
    profile_name = metadata.get("profile", "standard")
    
    # 2. Check Webhook Configuration
    config = load_pipeline_config()
    profile_data = config.get("profiles", {}).get(profile_name, {})
    webhook_url = profile_data.get("webhook_url")
    
    if not webhook_url:
        # Silently exit if this profile doesn't require n8n dispatch
        sys.exit(0)
        
    # 3. Extract the final payload
    final_txt_files = glob.glob(os.path.join(workspace_dir, "*_final.txt"))
    if not final_txt_files:
        print(f"[Dispatcher] ⚠️ No _final.txt found in {workspace_dir}. Aborting.")
        sys.exit(1)
        
    target_file = final_txt_files[0]
    with open(target_file, 'r', encoding='utf-8') as f:
        raw_content = f.read().strip()
        
    # 4. Smart Payload Packaging
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        payload = {"text_content": raw_content}
        
    print(f"[Dispatcher] Routing '{profile_name}' payload to n8n...")
    push_to_n8n(webhook_url, payload, workspace_name)

if __name__ == "__main__":
    main()