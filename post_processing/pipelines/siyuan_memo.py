from pathlib import Path
from .base import BasePipeline
from core.text_tools import regex_replacer, grammar_checker
from typing import Optional
from pydantic import BaseModel, Field

# --- 1. Generalized Semantic Schema ---
class SiyuanNote(BaseModel):
    title: str = Field(
        ..., description="A concise, highly descriptive title for the note."
    )
    suggested_themes: List[str] = Field(
        ..., description="2 to 3 broad conceptual categories representing the text (e.g., 'machine learning', 'logistics', 'personal ideas', 'shopping lists', 'projects'...). Do NOT attempt to guess specific application notebook names."
    )
    extracted_tags: List[str] = Field(
        ..., description="Specific, highly relevant keywords extracted directly from the text to be used as search tags."
    )
    markdown_content: str = Field(
        ..., description="The cleaned, logically structured body of the note formatted in standard Markdown (using headers, bullet points, and bold text where appropriate)."
    )

class SiyuanMemoPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[SiyuanMemoPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)
        
        # 1. Regex Replacer
        dict_path_str = self.profile_data.get("dictionary", "configs/hallucinations_dict.yaml")
        dict_path = str(self.repo_root / dict_path_str)
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        
        # 2. Grammar Checker
        current_text = grammar_checker(current_text, language=self.language, strip_markers=True)
                
        
        llm_cfg = self.profile_data["post_processing"][0]
        
        # Pass the schema directly to the base class LLM runner
        return self.call_llm(
            provider_type=llm_cfg["provider"],
            model=llm_cfg["model"],
            prompt_template=llm_cfg["prompt"],
            input_text=current_text,
            endpoint=llm_cfg.get("endpoint"),
            schema=SiyuanNote
        )