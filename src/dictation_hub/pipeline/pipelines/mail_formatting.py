from pathlib import Path

from dictation_hub.pipeline.core.text_tools import grammar_checker, regex_replacer
from dictation_hub.pipeline.pipelines.base import BasePipeline



class MailFormattingPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[MailFormattingPipeline] Executing deterministic extraction...")
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
        current_text = self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
        )
        return current_text
