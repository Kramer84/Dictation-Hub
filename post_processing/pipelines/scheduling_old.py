from pathlib import Path
import datetime
from core.text_tools import regex_replacer, grammar_checker
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from .base import BasePipeline


# --- 1. Structured Enumerations for Recurrence ---
class RecurrenceRule(BaseModel):
    frequency: Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"] = Field(
        ..., description="The strict base frequency of the repeating event."
    )
    interval: int = Field(
        1,
        description="The interval spacing (e.g., 1 for every week, 2 for every other week).",
    )
    by_day: Optional[List[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]]] = Field(
        None,
        description="Specific days of the week the event occurs on. Essential for WEEKLY frequencies.",
    )


# --- 2. Advanced Logistic Data Validation ---
class ScheduleEvent(BaseModel):
    # 1. Reasoning field placed FIRST to establish contextual math tokens
    reasoning_trace: str = Field(
        ..., 
        description="Calculate the exact calendar date. State today's date and day of the week from the CURRENT_CONTEXT, then count forward step-by-step to the requested day to determine the final YYYY-MM-DD date."
    )
    # 2. Standard extraction fields follow
    intent_type: Literal["single_event", "date_range", "recurring_event"] = Field(
        ..., description="Strictly classifies the structural nature of the calendar entry."
    )
    title: str = Field(..., description="A concise, professional title for the event.")
    start_datetime: str = Field(
        ..., description="ISO 8601 format (YYYY-MM-DDTHH:MM:SS). If a broad block is dictated (e.g., 'morning'), default to 08:00:00. For 'afternoon', default to 13:00:00."
    )
    end_datetime: Optional[str] = Field(
        None, description="ISO 8601 format. If a broad block (e.g., 'morning') is dictated, set to 12:00:00. Otherwise, leave null."
    )
    duration_minutes: int = Field(
        60, description="Duration in minutes. Defaults to 60 for specific events. For broad blocks, calculate total span."
    )
    location: Optional[str] = Field(
        None, description="Physical address, city, building, room number, or virtual meeting link."
    )
    description: Optional[str] = Field(
        None, description="Additional context, agenda items, notes, or general information regarding the event."
    )
    recurrence: Optional[RecurrenceRule] = Field(
        None, description="Recurrence mechanics. MUST be populated if intent_type is recurring_event."
    )

    @field_validator("start_datetime", "end_datetime")
    @classmethod
    def parse_and_format_iso(cls, v):
        if v is None:
            return v
        try:
            # 1. Clean up LLM hallucinations
            clean_v = v.replace("Z", "")
            if "T" not in clean_v and len(clean_v) <= 10:
                clean_v += "T00:00:00"

            # 2. Parse the string
            dt = datetime.datetime.fromisoformat(clean_v)
            dt_naive = dt.replace(tzinfo=None)

            # 3. Time-Travel Defense
            grace_period = datetime.datetime.now() - datetime.timedelta(days=2)
            if dt_naive < grace_period:
                raise ValueError(
                    f"Temporal logic failure: {dt_naive.date()} is too far in the past. "
                    f"Events must be scheduled for the present or future."
                )

            return dt_naive.strftime("%Y-%m-%dT%H:%M:%S")

        except ValueError as e:
            if "Temporal logic failure" in str(e):
                raise e
            raise ValueError(
                f"Invalid ISO 8601 format: '{v}'. Expected format is YYYY-MM-DDTHH:MM:SS."
            )

    @model_validator(mode="after")
    def sync_duration_and_endtime(self):
        # 1. Base parsing
        dt_start = datetime.datetime.fromisoformat(self.start_datetime)

        # 2. Mathematical Auto-Correction
        if self.end_datetime is None:
            # If the LLM didn't give an end time, calculate it using the duration (default 60 mins)
            dt_end = dt_start + datetime.timedelta(minutes=self.duration_minutes)
            self.end_datetime = dt_end.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            # If the LLM DID give an end time (like 13:00 to 18:00), force the duration to match the math
            dt_end = datetime.datetime.fromisoformat(self.end_datetime)
            calculated_duration = int((dt_end - dt_start).total_seconds() / 60)
            self.duration_minutes = calculated_duration

        # 3. Rule A: The arrow of time
        if dt_end < dt_start:
            raise ValueError(
                "Logical error: 'end_datetime' cannot occur before 'start_datetime'."
            )

        # 4. Rule B: Contextual integrity
        if (
            self.intent_type == "date_range"
            and (dt_end - dt_start).total_seconds() < 86400
        ):
            # Date ranges should generally span at least a day
            pass

        if self.intent_type == "recurring_event" and not self.recurrence:
            raise ValueError(
                "Logical error: 'recurring_event' intent strictly requires a 'recurrence' object payload."
            )

        if self.intent_type == "single_event" and self.recurrence is not None:
            raise ValueError(
                "Logical error: 'single_event' cannot contain a 'recurrence' payload."
            )

        return self

    @model_validator(mode="after")
    def check_cross_field_logistics(self):
        # Rule A: The arrow of time (End must be >= Start)
        if self.end_datetime:
            dt_start = datetime.datetime.fromisoformat(self.start_datetime)
            dt_end = datetime.datetime.fromisoformat(self.end_datetime)
            if dt_end < dt_start:
                raise ValueError(
                    "Logical error: 'end_datetime' cannot occur before 'start_datetime'."
                )

        # Rule B: Contextual integrity based on intent_type
        if self.intent_type == "date_range" and not self.end_datetime:
            raise ValueError(
                "Logical error: 'date_range' intent strictly requires an 'end_datetime'."
            )

        if self.intent_type == "recurring_event" and not self.recurrence:
            raise ValueError(
                "Logical error: 'recurring_event' intent strictly requires a 'recurrence' object payload."
            )

        if self.intent_type == "single_event" and self.recurrence is not None:
            raise ValueError(
                "Logical error: 'single_event' cannot contain a 'recurrence' payload."
            )

        return self


class SchedulingPipeline(BasePipeline):
    def execute(self, input_json_path: Path) -> str:
        print("[SchedulingPipeline] Executing deterministic extraction...")
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

        llm_cfg = self.profile_data["post_processing"][0]

        # Multi-Pass Cognitive Verification Loop
        max_retries = 3
        for attempt in range(max_retries):
            raw_output = self.call_llm(
                provider_type=llm_cfg["provider"],
                model=llm_cfg["model"],
                prompt_template=llm_cfg["prompt"],
                input_text=current_text,
                endpoint=llm_cfg.get("endpoint"),
                schema=ScheduleEvent,
            )

            try:
                # Attempt strict structural and logical validation
                validated_object = ScheduleEvent.model_validate_json(raw_output)
                print(
                    "[SchedulingPipeline] JSON Schema and Logistic Business Logic validated successfully."
                )
                return validated_object.model_dump_json(indent=2)

            except ValidationError as e:
                print(
                    f"⚠️ [SchedulingPipeline] Validation Error on attempt {attempt + 1}: {e}"
                )
                if attempt == max_retries - 1:
                    print(
                        "❌ [SchedulingPipeline] Max validation retries reached. Returning error payload."
                    )
                    return f'{{ "error": "Validation failed after {max_retries} attempts", "raw_output": {repr(raw_output)} }}'

                # Feedback loop: Dynamically append the exact Python Error back to the LLM's context
                current_text += f"\n\n[SYSTEM ERROR IN PREVIOUS PASS]: Your previous JSON output failed validation. Correct the following strict logistic errors:\n{str(e)}"

        return raw_output
