#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

# Add post_processing directory to path for internal imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.static_config import WhisperPipelineConfig
from pipelines.standard import StandardPipeline
from pipelines.technical import TechnicalPipeline
from pipelines.mail_formatting import MailFormattingPipeline
from pipelines.siyuan_memo import SiyuanMemoPipeline
from pipelines.mail_drafting import MailDraftingPipeline
from pipelines.scheduling import SchedulingPipeline
from pipelines.cli_coder import CLICoderPipeline

# Pipeline Registry
PIPELINE_MAP = {
    "standard": StandardPipeline,
    "technical": TechnicalPipeline,
    "mail_drafting": MailDraftingPipeline,
    "mail_formatting": MailFormattingPipeline,
    "scheduling": SchedulingPipeline,
    "cli_coder": CLICoderPipeline,
    "siyuan_memo": SiyuanMemoPipeline,
}


def main():
    parser = argparse.ArgumentParser(
        description="Orchestration Engine for Post-Processing"
    )
    parser.add_argument(
        "--profile", required=True, help="The pipeline profile to execute"
    )
    parser.add_argument(
        "--input", required=True, help="Path to the Whisper _full.json file"
    )
    parser.add_argument(
        "--workspace", required=True, help="Path to the current execution workspace"
    )
    args = parser.parse_args()

    repo_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    static_config_path = repo_root / "configs" / "static.json"
    pipeline_config_path = repo_root / "configs" / "pipeline_config.json"

    # 1. Instantiate Static Configuration
    static_config = WhisperPipelineConfig.load_from_file(static_config_path)

    # 2. Load Pipeline Configuration
    with open(pipeline_config_path, "r", encoding="utf-8") as f:
        full_config = json.load(f)

    # 3. Validation: Does the profile exist in JSON?
    user_info = full_config.get("user_information", {})
    profile_data = full_config.get("profiles", {}).get(args.profile)
    if not profile_data:
        print(
            f"⚠️ [Engine] Warning: Profile '{args.profile}' not found in pipeline_config.json. Defaulting to 'standard'."
        )
        profile_data = full_config.get("profiles", {}).get("standard", {})
        args.profile = "standard"

    # 4. Validation: Does the profile map to a Python Pipeline Class?
    pipeline_class = PIPELINE_MAP.get(args.profile)
    if not pipeline_class:
        print(
            f"⚠️ [Engine] Warning: No Python class found for profile '{args.profile}' in PIPELINE_MAP. Falling back to StandardPipeline."
        )
        pipeline_class = StandardPipeline

    # 5. Initialization
    pipeline = pipeline_class(
        repo_root=repo_root,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=Path(args.workspace),
        user_information=user_info,
    )

    # 6. Execution
    print(f"[Engine] Booting {pipeline_class.__name__} for profile '{args.profile}'...")
    final_text = pipeline.execute(Path(args.input))

    # 7. Output resolution using static_config suffixes
    timestamp = pipeline.metadata.get("timestamp", "output")
    final_path = (
        Path(args.workspace) / f"{timestamp}{static_config.suffixes.final_text}"
    )

    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    print(f"✅ [Engine] Post-processing complete. Output saved to {final_path}")


if __name__ == "__main__":
    main()
