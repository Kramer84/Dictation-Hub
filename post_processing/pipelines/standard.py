from pathlib import Path
from .base import BasePipeline
from core.text_tools import regex_replacer, grammar_checker

class StandardPipeline(BasePipeline):
    # Configuration encapsulated within the Python class
    PIPELINE_CONFIG = {
        "dictionary": "configs/hallucinations_dict.yaml"
    }

    def execute(self, input_json_path: Path) -> str:
        print("[StandardPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)

        dict_path = str(self.repo_root / self.PIPELINE_CONFIG["dictionary"])
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        current_text = grammar_checker(current_text, language=self.language, strip_markers=True)

        return current_text