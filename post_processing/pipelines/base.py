import json
import os
import time
import datetime
from pathlib import Path
from core.text_tools import whisper_json_output_pre_treatment
from core.llm_providers import LocalProvider, MistralProvider, ChatMessage, LLMConfig

class BasePipeline:
    def __init__(self, repo_root: Path, static_config, profile_data: dict, workspace_dir: Path, user_information: dict = None):
        self.repo_root = repo_root
        self.static_config = static_config
        self.profile_data = profile_data
        self.workspace_dir = workspace_dir
        self.user_information = user_information if user_information else {}
        
        self.env_overrides = self.profile_data.get("env_overrides", {})
        self.mark_confidence = self.env_overrides.get("MARK_CONFIDENCE", "false").lower() == "true"
        self.compress_reps = self.env_overrides.get("COMPRESS_REPETITIONS", "false").lower() == "true"
        
        # Load Metadata once as the source of truth for the session
        self.metadata = self._load_metadata()
        self.language = self.metadata.get("language", "en")

    def _load_metadata(self) -> dict:
        metadata_path = self.workspace_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"language": "en"}

    def apply_deterministic_cleaner(self, input_json_path: Path) -> str:
        """Runs the JSON pre-treatment and generates the raw text footprint."""
        whisper_json_output_pre_treatment(
            transcription_json_path=str(input_json_path),
            static_config=self.static_config,
            mark_confidence=self.mark_confidence,
            compress_repetitions=self.compress_reps
        )
        
        # Dynamically resolve paths using configuration definitions
        base_name = str(input_json_path).replace(self.static_config.suffixes.full_json, "")
        cleaned_json = f"{base_name}{self.static_config.suffixes.cleaned_json}"
        
        with open(cleaned_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        raw_text = " ".join([seg["text"] for seg in data.get("segments", [])]).strip()
        
        # Write out raw text so execution_router.sh can diff it against final_text
        timestamp = self.metadata.get("timestamp", "unknown")
        raw_txt_path = self.workspace_dir / f"{timestamp}_raw.txt"
        with open(raw_txt_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
            
        return raw_text

    def call_llm(self, provider_type: str, model: str, prompt_template: str, input_text: str, endpoint: str = "http://localhost:11434/v1/chat/completions", schema=None) -> str:
        """Standardized LLM execution with the <DICTATION> quarantine wrapper."""
        system_prompt = prompt_template.replace("{language}", self.language)
        
        # 1. Inject Dynamic Context (Temporal & Spatial)
        now = datetime.datetime.now()
        local_tz = time.tzname[0] if not time.daylight else time.tzname[1]
        
        user_context_str = "\n".join([f"User {k.capitalize()}: {v}" for k, v in self.user_information.items()])
        
        context_header = (
            f"\n\n=== CURRENT_CONTEXT ===\n"
            f"Current Date: {now.strftime('%Y-%m-%d')}\n"
            f"Current Time: {now.strftime('%H:%M:%S')}\n"
            f"Day of Week: {now.strftime('%A')}\n"
            f"User Timezone: {local_tz}\n"
            f"{user_context_str}\n"
            f"=======================\n"
        )
        system_prompt += context_header
        
        # 2. Schema Grounding & SOT Alignment
        target_temperature = 0.1
        if schema:
            target_temperature = 0.0  # SOT Sec 5: Force deterministic output for structures
            schema_str = json.dumps(schema.model_json_schema(), indent=2)
            system_prompt += (
                f"\n\n=== MANDATORY JSON OUTPUT SCHEMA ===\n"
                f"Respond strictly in minified JSON matching the expected schema structure.\n"
                f"You MUST output a JSON object that strictly adheres to this JSON schema structure:\n"
                f"{schema_str}\n"
                f"Do not include markdown wrappers, thoughts, or extra fields outside this schema layout.\n"
                f"====================================\n"
            )

        # 3. Enforce quarantine block for prompt injection defense
        quarantined_text = f"<DICTATION>\n{input_text}\n</DICTATION>"
        
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=quarantined_text)
        ]
        
        if provider_type == "mistral":
            provider = MistralProvider(api_key=os.environ.get("MISTRAL_API_KEY"), model=model)
        else:
            provider = LocalProvider(endpoint=endpoint, model=model)
            
        print(f"[LLM] Prompting {model} via {provider_type}...")
        result = provider.generate(messages=messages, config=LLMConfig(), response_schema=schema)
        return result.raw_content

    def execute(self, input_json_path: Path) -> str:
        raise NotImplementedError("Subclasses must implement execute()")