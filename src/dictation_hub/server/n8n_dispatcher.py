import glob
import sys
import argparse
import json
import logging
from pathlib import Path
import requests

from dictation_hub.core.config_manager import USER_CONFIG_DIR

logger = logging.getLogger(__name__)

# Resolve paths elegantly
SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parents[2]

# Config fallback logic (User dir first, then repo defaults)
CONFIG_JSON_PATH = USER_CONFIG_DIR / "pipeline_config.json"
if not CONFIG_JSON_PATH.exists():
    CONFIG_JSON_PATH = REPO_ROOT / "configs" / "pipeline_config.json"

def load_pipeline_config() -> dict:
    try:
        return json.loads(CONFIG_JSON_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error(f"Pipeline config not found at {CONFIG_JSON_PATH}")
        sys.exit(1)


def push_to_n8n(webhook_url: str, payload: dict, workspace_name: str) -> bool:
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        print(f"[Dispatcher] ✅ Successfully pushed {workspace_name} to n8n.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[Dispatcher] ⚠️ Failed to reach n8n for {workspace_name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Synchronous n8n Webhook Dispatcher")
    parser.add_argument(
        "--workspace", required=True, help="Path to the completed workspace"
    )
    args = parser.parse_args()
    workspace_dir = args.workspace
    workspace_name = os.path.basename(workspace_dir)
    meta_path = os.path.join(workspace_dir, "metadata.json")
    if not os.path.exists(meta_path):
        print(f"[Dispatcher] ⚠️ No metadata.json found in {workspace_dir}. Aborting.")
        sys.exit(1)
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    profile_name = metadata.get("profile", "standard")
    config = load_pipeline_config()
    profile_data = config.get("profiles", {}).get(profile_name, {})
    webhook_url = profile_data.get("webhook_url")
    if not webhook_url:
        sys.exit(0)
    final_txt_files = glob.glob(os.path.join(workspace_dir, "*_final.txt"))
    if not final_txt_files:
        print(f"[Dispatcher] ⚠️ No _final.txt found in {workspace_dir}. Aborting.")
        sys.exit(1)
    target_file = final_txt_files[0]
    with open(target_file, "r", encoding="utf-8") as f:
        raw_content = f.read().strip()
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        payload = {"text_content": raw_content}
    print(f"[Dispatcher] Routing '{profile_name}' payload to n8n...")
    push_to_n8n(webhook_url, payload, workspace_name)



if __name__ == "__main__":
    main()
