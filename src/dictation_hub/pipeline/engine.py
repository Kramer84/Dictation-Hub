import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.static_config import WhisperPipelineConfig
from pipelines.cli_coder import CLICoderPipeline
from pipelines.mail_drafting import MailDraftingPipeline
from pipelines.mail_formatting import MailFormattingPipeline
from pipelines.scheduling import SchedulingPipeline
from pipelines.siyuan_memo import SiyuanMemoPipeline
from pipelines.standard import StandardPipeline
from pipelines.technical import TechnicalPipeline

logger = logging.getLogger(__name__)

PIPELINE_MAP = {
    "standard": StandardPipeline,
    "technical": TechnicalPipeline,
    "mail_drafting": MailDraftingPipeline,
    "mail_formatting": MailFormattingPipeline,
    "scheduling": SchedulingPipeline,
    "cli_coder": CLICoderPipeline,
    "siyuan_memo": SiyuanMemoPipeline,
}


def setup_logging(workspace_dir: Path = None):

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if workspace_dir:
        log_file = workspace_dir / "orchestrator.log"
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        logger.debug("File logging initialized at %s", log_file)


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

    workspace_path = Path(args.workspace)

    setup_logging(workspace_path)

    logger.info("Starting Orchestration Engine")
    logger.debug(
        "Parsed arguments: profile='%s', input='%s', workspace='%s'",
        args.profile,
        args.input,
        args.workspace,
    )

    repo_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    logger.debug("Resolved repo_root path: %s", repo_root)

    static_config_path = repo_root / "configs" / "static.json"
    pipeline_config_path = repo_root / "configs" / "pipeline_config.json"

    logger.debug("Loading static configuration from: %s", static_config_path)
    static_config = WhisperPipelineConfig.load_from_file(static_config_path)
    logger.debug("Static configuration loaded successfully.")

    logger.debug("Loading pipeline configuration from: %s", pipeline_config_path)
    with open(pipeline_config_path, "r", encoding="utf-8") as f:
        full_config = json.load(f)
    logger.debug(
        "Pipeline configuration loaded. Extracted %d root keys.",
        len(full_config.keys()),
    )

    user_info = full_config.get("user_information", {})
    logger.debug(
        "Extracted user_information mapping (keys: %s)", list(user_info.keys())
    )

    profile_data = full_config.get("profiles", {}).get(args.profile)
    logger.debug("Attempting to load profile data for '%s'", args.profile)

    if not profile_data:
        logger.warning(
            "⚠️ [Engine] Profile '%s' not found in pipeline_config.json. Defaulting to 'standard'.",
            args.profile,
        )
        profile_data = full_config.get("profiles", {}).get("standard", {})
        args.profile = "standard"
    else:
        logger.debug("Profile data for '%s' loaded successfully.", args.profile)

    pipeline_class = PIPELINE_MAP.get(args.profile)
    if not pipeline_class:
        logger.warning(
            "⚠️ [Engine] No Python class found for profile '%s' in PIPELINE_MAP. Falling back to StandardPipeline.",
            args.profile,
        )
        pipeline_class = StandardPipeline

    logger.debug("Instantiating pipeline class: %s", pipeline_class.__name__)
    pipeline = pipeline_class(
        repo_root=repo_root,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=workspace_path,
        user_information=user_info,
    )

    logger.info(
        "[Engine] Booting %s for profile '%s'...", pipeline_class.__name__, args.profile
    )

    input_path = Path(args.input)
    logger.debug("Executing pipeline. Input file: %s", input_path)
    final_text = pipeline.execute(input_path)
    logger.debug(
        "Pipeline execution complete. Output text length: %d characters.",
        len(final_text),
    )

    timestamp = pipeline.metadata.get("timestamp", "output")
    logger.debug("Extracted timestamp from metadata: '%s'", timestamp)

    final_path = workspace_path / f"{timestamp}{static_config.suffixes.final_text}"
    logger.debug("Writing final output to: %s", final_path)

    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    logger.info("✅ [Engine] Post-processing complete. Output saved to %s", final_path)



if __name__ == "__main__":
    main()
