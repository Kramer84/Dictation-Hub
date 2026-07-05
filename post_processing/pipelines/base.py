import datetime
import json
import logging
import os
import time
from pathlib import Path

from core.llm_providers import ChatMessage, LLMConfig, LocalProvider, MistralProvider
from core.text_tools import whisper_json_output_pre_treatment

logger = logging.getLogger(__name__)


class BasePipeline:
    def __init__(
        self,
        repo_root: Path,
        static_config,
        profile_data: dict,
        workspace_dir: Path,
        user_information: dict = None,
    ):
        logger.info("Initializing BasePipeline...")
        self.repo_root = repo_root
        self.static_config = static_config
        self.profile_data = profile_data
        self.workspace_dir = workspace_dir
        self.user_information = user_information if user_information else {}
        logger.debug(
            "Paths - Repo Root: %s | Workspace: %s", self.repo_root, self.workspace_dir
        )
        logger.debug("Loaded profile data keys: %s", list(self.profile_data.keys()))
        self.env_overrides = self.profile_data.get("env_overrides", {})
        self.mark_confidence = (
            self.env_overrides.get("MARK_CONFIDENCE", "false").lower() == "true"
        )
        self.compress_reps = (
            self.env_overrides.get("COMPRESS_REPETITIONS", "false").lower() == "true"
        )
        logger.debug(
            "Configuration flags - MARK_CONFIDENCE: %s | COMPRESS_REPETITIONS: %s",
            self.mark_confidence,
            self.compress_reps,
        )
        self.metadata = self._load_metadata()
        self.language = self.metadata.get("language", "en")
        logger.info("BasePipeline initialized with session language: %s", self.language)

    def _load_metadata(self) -> dict:
        metadata_path = self.workspace_dir / "metadata.json"
        logger.debug("Attempting to load metadata from: %s", metadata_path)
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.debug("Metadata loaded successfully: %s", data)
                return data
        logger.warning(
            "Metadata file not found at %s. Falling back to default metadata.",
            metadata_path,
        )
        return {"language": "en"}

    def apply_deterministic_cleaner(self, input_json_path: Path) -> str:
        logger.info(
            "Applying deterministic cleaner to transcription JSON: %s", input_json_path
        )
        whisper_json_output_pre_treatment(
            transcription_json_path=str(input_json_path),
            static_config=self.static_config,
            mark_confidence=self.mark_confidence,
            compress_repetitions=self.compress_reps,
        )
        logger.debug(
            "whisper_json_output_pre_treatment routine completed successfully."
        )
        base_name = str(input_json_path).replace(
            self.static_config.suffixes.full_json, ""
        )
        cleaned_json = f"{base_name}{self.static_config.suffixes.cleaned_json}"
        logger.debug("Resolved cleaned JSON target path: %s", cleaned_json)
        with open(cleaned_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        logger.debug("Extracted %d segments from the cleaned JSON file.", len(segments))
        raw_text = " ".join([seg["text"] for seg in segments]).strip()
        logger.debug(
            "Aggregated raw text block (Length: %d characters).", len(raw_text)
        )
        timestamp = self.metadata.get("timestamp", "unknown")
        raw_txt_path = self.workspace_dir / f"{timestamp}_raw.txt"
        logger.info("Writing deterministic raw text output to: %s", raw_txt_path)
        with open(raw_txt_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        return raw_text

    def call_llm(
        self,
        provider_type: str,
        model: str,
        prompt_template: str,
        input_text: str,
        endpoint: str = "http://localhost:11434/v1/chat/completions",
        schema=None,
    ) -> str:
        logger.info("Preparing LLM invocation via %s (Model: %s)", provider_type, model)
        logger.debug(
            "Input payload size: %d chars | Template size: %d chars",
            len(input_text),
            len(prompt_template),
        )
        system_prompt = prompt_template.replace("{language}", self.language)
        now = datetime.datetime.now()
        local_tz = time.tzname[0] if not time.daylight else time.tzname[1]
        user_context_str = "\n".join(
            [f"User {k.capitalize()}: {v}" for k, v in self.user_information.items()]
        )
        context_header = f"\n\n=== CURRENT_CONTEXT ===\nCurrent Date: {now.strftime('%Y-%m-%d')}\nCurrent Time: {now.strftime('%H:%M:%S')}\nDay of Week: {now.strftime('%A')}\nUser Timezone: {local_tz}\n{user_context_str}\n=======================\n"
        system_prompt += context_header
        logger.debug(
            "Temporal and spatial context header dynamically injected into system prompt."
        )
        target_temperature = 0.1
        if schema:
            logger.debug(
                "JSON Schema provided. Enforcing deterministic output with temperature=0.0."
            )
            target_temperature = 0.0
            schema_str = json.dumps(schema.model_json_schema(), indent=2)
            system_prompt += f"\n\n=== MANDATORY JSON OUTPUT SCHEMA ===\nRespond strictly in minified JSON matching the expected schema structure.\nYou MUST output a JSON object that strictly adheres to this JSON schema structure:\n{schema_str}\nDo not include markdown wrappers, thoughts, or extra fields outside this schema layout.\n====================================\n"
        quarantined_text = f"<DICTATION>\n{input_text}\n</DICTATION>"
        logger.debug("Input text quarantined within <DICTATION> tags.")
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=quarantined_text),
        ]
        if provider_type == "mistral":
            logger.debug("Instantiating MistralProvider.")
            provider = MistralProvider(
                api_key=os.environ.get("MISTRAL_API_KEY"), model=model
            )
        else:
            logger.debug("Instantiating LocalProvider bound to endpoint: %s", endpoint)
            provider = LocalProvider(endpoint=endpoint, model=model)
        logger.info("Executing API request to %s...", provider_type)
        result = provider.generate(
            messages=messages,
            config=LLMConfig(temperature=target_temperature, keep_alive="1m"),
            response_schema=schema,
        )
        logger.debug(
            "LLM generation successfully completed. Received %d characters.",
            len(result.raw_content),
        )
        return result.raw_content

    def execute(self, input_json_path: Path) -> str:
        logger.error(
            "BasePipeline.execute() called directly. Subclasses must implement this method."
        )
        raise NotImplementedError("Subclasses must implement execute()")
