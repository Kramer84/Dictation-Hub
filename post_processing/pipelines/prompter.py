import logging
from pathlib import Path
from core.text_tools import grammar_checker, regex_replacer
from .base import BasePipeline

logger = logging.getLogger(__name__)

class PrompterPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        logger.info("Executing PrompterPipeline for: %s", input_json_path)
        
        # 1. Deterministic Cleaning & Dictionary Resolution
        current_text = self.apply_deterministic_cleaner(input_json_path)
        dict_path_str = self.profile_data.get("dictionary", "configs/hallucinations_dict.yaml")
        dict_path = str(self.repo_root / dict_path_str)
        
        # 2. Text Normalization
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        current_text = grammar_checker(current_text, language=self.language, strip_markers=True)
        
        # 3. LLM Prompt Synthesis
        llm_cfg = self.profile_data["post_processing"][0]
        raw_output = self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
        )
        
        # 4. Clean Output (Strip code fences if local model adds them)
        clean_prompt = raw_output.strip()
        if clean_prompt.startswith("```"):
            lines = clean_prompt.split("\n")
            if len(lines) >= 2 and lines[-1].strip() == "```":
                clean_prompt = "\n".join(lines[1:-1]).strip()
                
        logger.info("PrompterPipeline successfully compiled new system prompt.")
        return clean_prompt