import logging
from pathlib import Path

from core.text_tools import grammar_checker, regex_replacer

from .base import BasePipeline

# Initialize the logger for this specific module
logger = logging.getLogger(__name__)


class CLICoderPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        logger.info("Executing CLICoderPipeline deterministic extraction for: %s", input_json_path)
        
        # 1. Deterministic Cleaning
        logger.debug("Applying deterministic cleaner...")
        current_text = self.apply_deterministic_cleaner(input_json_path)
        logger.debug("Deterministic cleaner output length: %d characters", len(current_text) if current_text else 0)
        
        # 2. Dictionary Resolution
        dict_path_str = self.profile_data.get("dictionary")
        if dict_path_str:
            logger.debug("Found dictionary config in profile: %s", dict_path_str)
        else:
            dict_path_str = "configs/hallucinations_dict.yaml"
            logger.debug("Dictionary config not found in profile; falling back to default: %s", dict_path_str)
            
        dict_path = str(self.repo_root / dict_path_str)
        logger.debug("Resolved absolute dictionary path: %s", dict_path)
        
        # 3. Regex Replacement
        logger.debug("Running regex_replacer with strip_markers=True...")
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        logger.debug("regex_replacer complete. Current text length: %d characters", len(current_text) if current_text else 0)
        
        # 4. Grammar Checking
        logger.debug("Running grammar_checker for language: %s...", getattr(self, 'language', 'unknown'))
        current_text = grammar_checker(
            current_text, language=self.language, strip_markers=True
        )
        logger.debug("grammar_checker complete. Current text length: %d characters", len(current_text) if current_text else 0)
        
        # 5. Config Extraction
        try:
            llm_cfg = self.profile_data["post_processing"][0]
            logger.debug("Successfully extracted post_processing LLM config.")
        except (KeyError, IndexError) as e:
            logger.error("Failed to retrieve post_processing LLM config. Error: %s", e)
            raise
            
        # 6. LLM Execution
        logger.info(
            "Calling LLM Pipeline (Provider: %s, Model: %s)...", 
            llm_cfg.get("provider"), 
            llm_cfg.get("model")
        )
        logger.debug(
            "LLM endpoint: %s | Prompt template: %s | Input text length sent to LLM: %d", 
            llm_cfg.get("endpoint"), 
            llm_cfg.get("prompt"),
            len(current_text) if current_text else 0
        )
        
        raw_output = self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
        )
        logger.debug("Received raw LLM output. Length: %d characters", len(raw_output) if raw_output else 0)
        
        # 7. Post-Processing / Code Extraction
        clean_code = raw_output.strip()
        logger.debug("Stripped whitespace from raw LLM output.")
        
        if clean_code.startswith("```"):
            logger.debug("Detected markdown code blocks in LLM output. Attempting to strip fences...")
            lines = clean_code.split("\n")
            if len(lines) >= 2 and lines[-1].strip() == "```":
                clean_code = "\n".join(lines[1:-1]).strip()
                logger.debug("Successfully stripped markdown fences. Final clean code length: %d characters", len(clean_code))
            else:
                logger.warning("Markdown fences detected but could not be cleanly stripped. Check LLM output formatting.")
                
        logger.info("CLICoderPipeline execution completed successfully.")
        return clean_code