from pathlib import Path
from typing import List, Optional

from dictation_hub.pipeline.core.text_tools import grammar_checker, regex_replacer
from pydantic import BaseModel, Field

from dictation_hub.pipeline.pipelines.base import BasePipeline



class SiyuanNote(BaseModel):
    title: str = Field(
        ..., description="A concise, highly descriptive title for the note."
    )
    suggested_themes: List[str] = Field(
        ...,
        description="2 to 3 broad conceptual categories representing the text (e.g., 'machine learning', 'logistics', 'personal ideas', 'shopping lists', 'projects'...). Do NOT attempt to guess specific application notebook names.",
    )
    extracted_tags: List[str] = Field(
        ...,
        description="Specific, highly relevant keywords extracted directly from the text to be used as search tags.",
    )
    markdown_content: str = Field(
        ...,
        description="The cleaned, logically structured body of the note formatted in standard Markdown (using headers, bullet points, and bold text where appropriate).",
    )


class SiyuanMemoPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[SiyuanMemoPipeline] Executing deterministic extraction...")
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
        return self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
            schema=SiyuanNote,
        )
