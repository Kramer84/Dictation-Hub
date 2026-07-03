from pathlib import Path
from .base import BasePipeline
from core.text_tools import regex_replacer, grammar_checker


class CLICoderPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[CLICoderPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)

        # 1. Regex Replacer
        dict_path_str = self.profile_data.get(
            "dictionary", "configs/hallucinations_dict.yaml"
        )
        dict_path = str(self.repo_root / dict_path_str)
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)

        # 2. Grammar Checker
        current_text = grammar_checker(
            current_text, language=self.language, strip_markers=True
        )

        # 3. LLM Injection
        llm_cfg = self.profile_data["post_processing"][0]
        
        raw_output = self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint")
        )
        
        # 3. Defensive Markdown Stripping
        clean_code = raw_output.strip()
        if clean_code.startswith("```"):
            # Split the text by lines
            lines = clean_code.split("\n")
            # Remove the first line (e.g., ```bash) and the last line (```)
            if len(lines) >= 2 and lines[-1].strip() == "```":
                clean_code = "\n".join(lines[1:-1]).strip()
                
        return clean_code