from pathlib import Path
import datetime
import dateparser
import json
from core.text_tools import regex_replacer, grammar_checker
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator, ValidationError
from .base import BasePipeline


# --- 1. Structured Enumerations for Recurrence ---
class RecurrenceRule(BaseModel):
    frequency: Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"] = Field(
        ..., description="The strict base frequency of the repeating event."
    )
    interval: int = Field(
        1, description="The interval spacing (e.g., 1 for every week, 2 for every other week)."
    )
    by_day: Optional[List[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]]] = Field(
        None, description="Specific days of the week the event occurs on. Essential for WEEKLY frequencies."
    )
    until: Optional[str] = Field(
        None, description="ISO 8601 end date. Strictly use this for long macro-durations (e.g., 'for 6 months'). Estimate the date."
    )
    count: Optional[int] = Field(
        None, description="Strictly the integer number of occurrences. Use this for exact micro-frequencies like 'for 4 weeks' (count=4). Do NOT use this for '6 months'."
    )

# --- 2. Step 1: LLM Extractor Schema (Pure NLP, No Math) ---
class LLMEventExtraction(BaseModel):
    intent_type: Literal["single_event", "date_range", "recurring_event"] = Field(
        ..., description="Strictly classifies the structural nature of the calendar entry."
    )
    title: str = Field(..., description="A concise, professional title for the event.")
    
    raw_start_date: str = Field(
        ..., 
        description="The specific date or relative day mentioned (e.g., 'next Tuesday', 'tomorrow', 'mardi prochain', 'Monday'). For recurring events, extract ONLY the first day it occurs. Do NOT include times."
    )
    raw_start_time: Optional[str] = Field(
        None, 
        description="The exact literal start time or broad time block mentioned (e.g., '1.30pm', '14h', 'morning', 'matin'). Extract the exact text from the prompt without altering it. Do NOT convert to 24-hour format."
    )
    raw_end_time: Optional[str] = Field(
        None, 
        description="The exact literal end time mentioned (e.g., '3.30pm', '15h30'). Extract the exact text from the prompt without altering it. Do NOT convert to 24-hour format. Leave null if absent."
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

    @model_validator(mode="after")
    def check_cross_field_logistics(self):
        if self.intent_type == "recurring_event" and not self.recurrence:
            raise ValueError("Logical error: 'recurring_event' intent strictly requires a 'recurrence' object payload.")
        if self.intent_type == "single_event" and self.recurrence is not None:
            raise ValueError("Logical error: 'single_event' cannot contain a 'recurrence' payload.")
        return self


# --- 3. Step 2: Final Output Schema (Strict Logistics) ---
class ScheduleEvent(BaseModel):
    intent_type: str
    title: str
    start_datetime: str
    end_datetime: str
    duration_minutes: int
    location: Optional[str]
    description: Optional[str]
    recurrence: Optional[RecurrenceRule]


class SchedulingPipeline(BasePipeline):
    
    def parse_temporal_components(self, date_phrase: str, time_phrase: Optional[str], base_date: datetime.datetime, prefer_time: str = "start") -> datetime.datetime:
        """Deterministically evaluates independent date and time strings into an exact datetime."""
        
        # 1. Convert FakeDatetime to pure datetime! 
        # freezegun's FakeDatetime causes dateparser's internal C-extensions to silently fail and return None.
        pure_base_date = datetime.datetime(
            base_date.year, base_date.month, base_date.day, 
            base_date.hour, base_date.minute, base_date.second
        )
        
        # 2. Strip modifiers that break dateparser's regex
        clean_date = date_phrase.lower()
        for mod in ["next week on", "next", "prochain", "upcoming"]:
            clean_date = clean_date.replace(mod, "")
        clean_date = clean_date.strip()

        date_settings = {'RELATIVE_BASE': pure_base_date}
        
        # Attempt Parse
        p_date = dateparser.parse(clean_date, languages=[self.language], settings=date_settings)
        if not p_date:
            p_date = dateparser.parse(clean_date, settings=date_settings)
            
        if not p_date:
            raise ValueError(f"Python dateparser failed to understand the date phrase: '{date_phrase}'")

        # 3. Mathematical Future Alignment (Replaces buggy PREFER_DATES_FROM='future')
        if p_date.date() < pure_base_date.date():
            # If the parsed date is in the past (e.g. Tuesday of the current week), 
            # bump it forward by 7 days to get the "next" occurrence safely.
            p_date += datetime.timedelta(days=7)

        # 4. Parse Time
        if time_phrase:
            time_settings = {
                'RELATIVE_BASE': pure_base_date,
                'TIMEZONE': 'UTC',
                'PARSERS': ['absolute-time'] # STRICTLY disables the 'relative-time'}
            }
            p_time = dateparser.parse(time_phrase, languages=[self.language], settings=time_settings)
            if not p_time:
                p_time = dateparser.parse(time_phrase, settings=time_settings)

            if p_time:
                return p_date.replace(hour=p_time.hour, minute=p_time.minute, second=0, microsecond=0)
            else:
                # Catch broad time blocks outputted into the time phrase
                lower_time = time_phrase.lower()
                if "morning" in lower_time or "matin" in lower_time:
                    return p_date.replace(hour=8, minute=0, second=0, microsecond=0)
                elif "afternoon" in lower_time or "après-midi" in lower_time:
                    return p_date.replace(hour=13, minute=0, second=0, microsecond=0)
                elif "evening" in lower_time or "soir" in lower_time:
                    return p_date.replace(hour=18, minute=0, second=0, microsecond=0)
                else:
                    raise ValueError(f"Python dateparser failed to understand the time phrase: '{time_phrase}'")

        # 5. Apply default broad-block hours if NO time was provided
        lower_date = date_phrase.lower()
        if "morning" in lower_date or "matin" in lower_date:
            return p_date.replace(hour=8, minute=0, second=0, microsecond=0)
        elif "afternoon" in lower_date or "après-midi" in lower_date:
            return p_date.replace(hour=13, minute=0, second=0, microsecond=0)
        elif "evening" in lower_date or "soir" in lower_date:
            return p_date.replace(hour=18, minute=0, second=0, microsecond=0)
        elif prefer_time == "end":
            return p_date.replace(hour=12, minute=0, second=0, microsecond=0)
        else:
            return p_date.replace(hour=8, minute=0, second=0, microsecond=0)

    def execute(self, input_json_path: Path) -> str:
        print("[SchedulingPipeline] Executing deterministic extraction...")
        current_text = self.apply_deterministic_cleaner(input_json_path)

        # 1. Regex Replacer
        dict_path_str = self.profile_data.get("dictionary", "configs/hallucinations_dict.yaml")
        dict_path = str(self.repo_root / dict_path_str)
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)

        # 2. Grammar Checker
        current_text = grammar_checker(current_text, language=self.language, strip_markers=True)

        llm_cfg = self.profile_data["post_processing"][0]

        # 3. Synchronize Python Clock with Metadata Context
        timestamp_str = self.metadata.get("timestamp")
        if timestamp_str:
            try:
                base_context_date = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            except ValueError:
                base_context_date = datetime.datetime.now()
        else:
            base_context_date = datetime.datetime.now()

        # Multi-Pass Cognitive Verification Loop
        max_retries = 3
        for attempt in range(max_retries):
            raw_output = self.call_llm(
                provider_type=llm_cfg["provider"],
                model=llm_cfg["model"],
                prompt_template=llm_cfg["prompt"],
                input_text=current_text,
                endpoint=llm_cfg.get("endpoint"),
                schema=LLMEventExtraction,  # Ask LLM for raw string extraction only
            )

            try:
                # 4. Validate LLM Extraction
                llm_data = LLMEventExtraction.model_validate_json(raw_output)
                print("[SchedulingPipeline] LLM Extracted Data successfully. Handing off to Python dateparser...")
                
                # 5. Execute Deterministic Temporal Math
                dt_start = self.parse_temporal_components(
                    date_phrase=llm_data.raw_start_date, 
                    time_phrase=llm_data.raw_start_time, 
                    base_date=base_context_date, 
                    prefer_time="start"
                )
                
                if llm_data.raw_end_time:
                    dt_end = self.parse_temporal_components(
                        date_phrase=llm_data.raw_start_date, 
                        time_phrase=llm_data.raw_end_time, 
                        base_date=base_context_date, 
                        prefer_time="end"
                    )
                else:
                    # Smart default durations for broad blocks (e.g. morning = 8am to 12pm)
                    combined_phrase = f"{llm_data.raw_start_date} {llm_data.raw_start_time or ''}".lower()
                    if "morning" in combined_phrase or "matin" in combined_phrase:
                        dt_end = dt_start.replace(hour=12, minute=0)
                    elif "afternoon" in combined_phrase or "après-midi" in combined_phrase:
                        dt_end = dt_start.replace(hour=17, minute=0)
                    elif "evening" in combined_phrase or "soir" in combined_phrase:
                        dt_end = dt_start.replace(hour=22, minute=0)
                    else:
                        dt_end = dt_start + datetime.timedelta(hours=1)
                
                # Auto-correct AM/PM discrepancies
                if dt_end < dt_start and dt_start.hour >= 12 and dt_end.hour < 12:
                    dt_end = dt_end + datetime.timedelta(hours=12)

                # Strict Arrow of Time check
                if dt_end < dt_start:
                    raise ValueError(f"Temporal Logic Failure: Calculated end time ({dt_end}) is before the start time ({dt_start}).")
                
                duration_mins = int((dt_end - dt_start).total_seconds() / 60)

                # 6. Construct the strictly validated output payload
                final_event = ScheduleEvent(
                    intent_type=llm_data.intent_type,
                    title=llm_data.title,
                    start_datetime=dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    end_datetime=dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                    duration_minutes=duration_mins,
                    location=llm_data.location,
                    description=llm_data.description,
                    recurrence=llm_data.recurrence
                )

                print("[SchedulingPipeline] Python Logistic Math finalized successfully.")
                return final_event.model_dump_json(indent=2)

            except (ValidationError, ValueError) as e:
                print(f"⚠️ [SchedulingPipeline] Validation/Parsing Error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    print("❌ [SchedulingPipeline] Max validation retries reached. Returning error payload.")
                    return json.dumps({
                            "error": f"Validation failed after {max_retries} attempts",
                            "raw_output": raw_output
                            })

                # Feedback loop: Dynamically append the exact error back to the LLM's context
                current_text += f"\n\n[SYSTEM ERROR IN PREVIOUS PASS]: Your previous JSON output caused a pipeline failure. Correct the following strict logistic errors:\n{str(e)}"

        return raw_output