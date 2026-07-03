import json
import re
import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
from .base import BasePipeline
from core.text_tools import regex_replacer, grammar_checker

class SemanticTemporalExtraction(BaseModel):
    """
    Aggressively flattened schema to prevent 0.6B model Schema Parroting.
    Replaces all Optional/Null fields with empty strings or zero defaults.
    """
    intent_type: Literal["single_event", "date_range", "recurring_event"] = Field(
        ..., description="The structural classification of the entry."
    )
    # RENAMED to 'event_name' to prevent JSON Schema "title" keyword collision with 0.6B models
    event_name: str = Field(..., description="Concise, professional event summary.") 
    
    # Day Extraction Elements
    day_modifier: Literal["today", "tomorrow", "day_after_tomorrow", ""] = Field(
        default="", description="Direct relative day shorthand. Leave empty if none."
    )
    day_of_week: Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", ""] = Field(
        default="", description="Explicitly named day of the week. Leave empty if none."
    )
    week_modifier: Literal["current", "next", "plus_two", ""] = Field(
        default="", description="Contextual week modifier (current, next, plus_two). Leave empty if none."
    )
    explicit_date: str = Field(
        default="", description="Absolute calendar date (YYYY-MM-DD). Leave empty if none."
    )

    # Granular Time Extraction Elements
    time_block: Literal["morning", "afternoon", "evening", "night", ""] = Field(
        default="", description="Broad temporal block. Leave empty if none."
    )
    specific_start_time: str = Field(
        default="", description="Explicit start time (e.g., '13:30', '1.30pm'). Leave empty if none."
    )
    specific_end_time: str = Field(
        default="", description="Explicit end time (e.g., '15:30'). Leave empty if none."
    )
    duration_minutes: int = Field(
        default=0, description="Duration of the meeting in minutes. Leave 0 if not stated."
    )
    
    # Recurrence metadata bounds
    recurrence_frequency: Literal["DAILY", "WEEKLY", "MONTHLY", ""] = Field(
        default="", description="Populate if recurring_event. Leave empty if none."
    )
    recurrence_duration_weeks: int = Field(
        default=0, description="Number of weeks recurrence lasts. Leave 0 if not stated."
    )

    location: str = Field(default="", description="Physical venue. Leave empty if none.")
    description: str = Field(default="", description="Context or agenda. Leave empty if none.")


class SchedulingPipeline(BasePipeline):
    def normalize_time_string(self, time_str: str) -> str:
        """Standardizes messy dictated times (13h30, 1.30pm) into clean HH:MM:SS format."""
        if not time_str:
            return "00:00:00"
        
        clean = time_str.lower().strip().replace(" ", "")
        clean = clean.replace("h", ":").replace(".", ":")
        
        match = re.match(r"(\d+)(?::(\d+))?(pm|am)?", clean)
        if not match:
            return "00:00:00"
            
        hours = int(match.group(1))
        minutes = int(match.group(2)) if match.group(2) else 0
        suffix = match.group(3)
        
        if suffix == "pm" and hours < 12:
            hours += 12
        elif suffix == "am" and hours == 12:
            hours = 0
            
        return f"{hours:02d}:{minutes:02d}:00"

    def reconstruct_absolute_datetime(self, extracted: SemanticTemporalExtraction) -> dict:
        """
        Deterministic Python Reconstruction Engine.
        Executes calendar math relative to the runtime context environment.
        """
        now_str = self.metadata.get("timestamp", datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            base_date = datetime.datetime.strptime(now_str.split("_")[0], "%Y%m%d")
        except ValueError:
            base_date = datetime.datetime.now()

        target_date = base_date

        if extracted.day_modifier:
            if extracted.day_modifier == "tomorrow":
                target_date += datetime.timedelta(days=1)
            elif extracted.day_modifier == "day_after_tomorrow":
                target_date += datetime.timedelta(days=2)
        
        elif extracted.day_of_week:
            days_map = {
                "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
                "Friday": 4, "Saturday": 5, "Sunday": 6
            }
            target_day_num = days_map.get(extracted.day_of_week, base_date.weekday())
            current_day_num = base_date.weekday()

            days_ahead = target_day_num - current_day_num
            mod = extracted.week_modifier or "current"
            
            if mod == "current":
                if days_ahead <= 0:  
                    days_ahead += 7
            elif mod == "next":
                if days_ahead <= 0:
                    days_ahead += 7
                days_ahead += 7
            elif mod == "plus_two":
                if days_ahead <= 0:
                    days_ahead += 7
                days_ahead += 14

            target_date += datetime.timedelta(days=days_ahead)

        elif extracted.explicit_date:
            try:
                target_date = datetime.datetime.strptime(extracted.explicit_date, "%Y-%m-%d")
            except ValueError:
                pass

        if extracted.specific_start_time:
            start_time_str = self.normalize_time_string(extracted.specific_start_time)
            if extracted.specific_end_time:
                end_time_str = self.normalize_time_string(extracted.specific_end_time)
                dt_start = datetime.datetime.fromisoformat(f"{target_date.strftime('%Y-%m-%d')}T{start_time_str}")
                dt_end = datetime.datetime.fromisoformat(f"{target_date.strftime('%Y-%m-%d')}T{end_time_str}")
                duration = int((dt_end - dt_start).total_seconds() / 60)
            else:
                dt_start = datetime.datetime.fromisoformat(f"{target_date.strftime('%Y-%m-%d')}T{start_time_str}")
                duration = extracted.duration_minutes or 60
                dt_end = dt_start + datetime.timedelta(minutes=duration)
        else:
            block_defaults = {
                "morning": ("08:00:00", 240),
                "afternoon": ("14:00:00", 240),
                "evening": ("18:00:00", 180),
                "night": ("21:00:00", 120)
            }
            time_info = block_defaults.get(extracted.time_block, ("08:00:00", 60))
            start_time_str = time_info[0]
            duration = extracted.duration_minutes or time_info[1]
            
            dt_start = datetime.datetime.fromisoformat(f"{target_date.strftime('%Y-%m-%d')}T{start_time_str}")
            dt_end = dt_start + datetime.timedelta(minutes=duration)

        recurrence_payload = None
        if extracted.intent_type == "recurring_event" and extracted.recurrence_frequency:
            by_day_val = [extracted.day_of_week[:2].upper()] if extracted.day_of_week else None
            recurrence_payload = {
                "frequency": extracted.recurrence_frequency,
                "interval": 1,
                "by_day": by_day_val,
                "until_weeks": extracted.recurrence_duration_weeks if extracted.recurrence_duration_weeks > 0 else None
            }

        return {
            "intent_type": extracted.intent_type,
            "title": extracted.event_name, # Map it back to standard 'title' format here
            "start_datetime": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_datetime": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_minutes": duration,
            "location": extracted.location if extracted.location else None,
            "description": extracted.description if extracted.description else None,
            "recurrence": recurrence_payload
        }

    def execute(self, input_json_path: Path) -> str:
        print("[SchedulingPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)

        dict_path_str = self.profile_data.get("dictionary", "configs/hallucinations_dict.yaml")
        dict_path = str(self.repo_root / dict_path_str)
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        current_text = grammar_checker(current_text, language=self.language, strip_markers=True)

        llm_cfg = self.profile_data["post_processing"][0]

        max_retries = 3
        for attempt in range(max_retries):
            raw_output = self.call_llm(
                provider_type=llm_cfg["provider"],
                model=llm_cfg["model"],
                prompt_template=llm_cfg["prompt"],
                input_text=current_text,
                endpoint=llm_cfg.get("endpoint"),
                schema=SemanticTemporalExtraction,
            )

            try:
                cleaned_json_string = raw_output.strip()
                if "<think>" in cleaned_json_string:
                    print("[SchedulingPipeline] Sanitizing reasoning tags out from text footprint...")
                    cleaned_json_string = cleaned_json_string.split("</think>")[-1].strip()

                extracted_tokens = SemanticTemporalExtraction.model_validate_json(cleaned_json_string)
                final_payload = self.reconstruct_absolute_datetime(extracted_tokens)
                return json.dumps(final_payload, indent=2)

            except (ValidationError, Exception) as e:
                print(f"⚠️ [SchedulingPipeline] Validation Error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    return json.dumps({
                        "error": f"Validation failed after {max_retries} attempts",
                        "exception_details": str(e),
                        "raw_output": raw_output
                    }, indent=2)

                current_text += f"\n\n[SYSTEM ERROR IN PREVIOUS PASS]: Your output failed to populate the structural schema correctly. Error details:\n{str(e)}"

        return raw_output