from pathlib import Path

from core.text_tools import grammar_checker, regex_replacer

from .base import BasePipeline


class CLICoderPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[CLICoderPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)
        dict_path_str = self.profile_data.get(
            "dictionary", "configs/hallucinations_dict.yaml"
        )
        dict_path = str(self.repo_root / dict_path_str)
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        current_text = grammar_checker(
            current_text, language=self.language, strip_markers=True
        )
        llm_cfg = self.profile_data["post_processing"][0]
        raw_output = self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
        )
        clean_code = raw_output.strip()
        if clean_code.startswith("```"):
            lines = clean_code.split("\n")
            if len(lines) >= 2 and lines[-1].strip() == "```":
                clean_code = "\n".join(lines[1:-1]).strip()
        return clean_code
