import datetime
import json
import logging
import re
import time
from pathlib import Path
from typing import List, Literal, Optional

import dateparser
from core.text_tools import grammar_checker, regex_replacer
from pydantic import BaseModel, Field, ValidationError, model_validator

from .base import BasePipeline

logger = logging.getLogger(__name__)


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
    until: Optional[str] = Field(
        None,
        description="ISO 8601 end date. Strictly use this for long macro-durations (e.g., 'for 6 months'). Estimate the date.",
    )
    count: Optional[int] = Field(
        None,
        description="Strictly the integer number of occurrences. Use this for exact micro-frequencies like 'for 4 weeks' (count=4). Do NOT use this for '6 months'.",
    )


class LLMEventExtraction(BaseModel):
    intent_type: Literal["single_event", "date_range", "recurring_event"] = Field(
        ...,
        description="Strictly classifies the structural nature of the calendar entry.",
    )
    title: str = Field(..., description="A concise, professional title for the event.")
    raw_start_date: str = Field(
        ...,
        description="The specific date or relative day mentioned (e.g., 'next Tuesday', 'tomorrow', 'mardi prochain', 'Monday'). For recurring events, extract ONLY the first day it occurs. Do NOT include times.",
    )
    raw_start_time: Optional[str] = Field(
        None,
        description="The exact literal start time or broad time block mentioned (e.g., '1.30pm', '14h', 'morning', 'matin'). Extract the exact text from the prompt without altering it. Do NOT convert to 24-hour format.",
    )
    raw_end_time: Optional[str] = Field(
        None,
        description="The exact literal end time mentioned (e.g., '3.30pm', '15h30'). Extract the exact text from the prompt without altering it. Do NOT convert to 24-hour format. Leave null if absent.",
    )
    location: Optional[str] = Field(
        None,
        description="Physical address, city, building, room number, or virtual meeting link.",
    )
    description: Optional[str] = Field(
        None,
        description="Additional context, agenda items, notes, or general information regarding the event.",
    )
    recurrence: Optional[RecurrenceRule] = Field(
        None,
        description="Recurrence mechanics. MUST be populated if intent_type is recurring_event.",
    )

    @model_validator(mode="after")
    def check_cross_field_logistics(self):
        if self.intent_type == "recurring_event" and (not self.recurrence):
            logger.error(
                "Cross-field validation failed: intent_type='recurring_event' but no recurrence payload was provided."
            )
            raise ValueError(
                "Logical error: 'recurring_event' intent strictly requires a 'recurrence' object payload."
            )
        if self.intent_type == "single_event" and self.recurrence is not None:
            logger.error(
                "Cross-field validation failed: intent_type='single_event' but a recurrence payload was provided: %s",
                self.recurrence,
            )
            raise ValueError(
                "Logical error: 'single_event' cannot contain a 'recurrence' payload."
            )
        logger.debug(
            "Cross-field validation passed for intent_type='%s'.", self.intent_type
        )
        return self


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
    def parse_temporal_components(
        self,
        date_phrase: str,
        time_phrase: Optional[str],
        base_date: datetime.datetime,
        prefer_time: str = "start",
    ) -> datetime.datetime:
        logger.debug(
            "parse_temporal_components() called with date_phrase=%r, time_phrase=%r, base_date=%s, prefer_time=%r",
            date_phrase,
            time_phrase,
            base_date.isoformat(),
            prefer_time,
        )
        pure_base_date = datetime.datetime(
            base_date.year,
            base_date.month,
            base_date.day,
            base_date.hour,
            base_date.minute,
            base_date.second,
        )
        logger.debug("Normalized pure_base_date=%s", pure_base_date.isoformat())
        clean_date = date_phrase.lower()
        for mod in ["next week on", "next", "prochain", "upcoming"]:
            if mod in clean_date:
                logger.debug("Stripping modifier %r from date phrase.", mod)
            clean_date = clean_date.replace(mod, "")
        clean_date = clean_date.strip()
        logger.debug("Cleaned date phrase: %r (from raw %r)", clean_date, date_phrase)
        date_settings = {"RELATIVE_BASE": pure_base_date}
        logger.debug(
            "Attempting dateparser.parse() on %r with language=%r",
            clean_date,
            self.language,
        )
        p_date = dateparser.parse(
            clean_date, languages=[self.language], settings=date_settings
        )
        if not p_date:
            logger.warning(
                "dateparser failed with language=%r for %r, retrying without language constraint.",
                self.language,
                clean_date,
            )
            p_date = dateparser.parse(clean_date, settings=date_settings)
        if not p_date:
            logger.error(
                "dateparser could not parse date phrase %r under any configuration.",
                date_phrase,
            )
            raise ValueError(
                f"Python dateparser failed to understand the date phrase: '{date_phrase}'"
            )
        logger.debug("dateparser resolved date phrase to: %s", p_date.isoformat())
        if p_date.date() < pure_base_date.date():
            logger.info(
                "Resolved date %s is before base date %s; rolling forward by 7 days.",
                p_date.date(),
                pure_base_date.date(),
            )
            p_date += datetime.timedelta(days=7)
            logger.debug("Date after roll-forward: %s", p_date.isoformat())
        if time_phrase:
            logger.debug(
                "Time phrase provided: %r. Attempting deterministic regex parse.",
                time_phrase,
            )
            clean_time = time_phrase.replace("\xa0", " ").strip().lower()
            time_match = re.search("(\\d{1,2})\\s*[hH:]\\s*(\\d{0,2})?", clean_time)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                result = p_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                logger.debug("Regex parsed %r to %02d:%02d", time_phrase, hour, minute)
                return result
            logger.debug(
                "Regex didn't match %r, falling back to dateparser.", time_phrase
            )
            time_settings = {
                "RELATIVE_BASE": pure_base_date,
                "TIMEZONE": "UTC",
                "PARSERS": ["absolute-time"],
            }
            p_time = dateparser.parse(
                time_phrase, languages=[self.language], settings=time_settings
            )
            if p_time:
                result = p_date.replace(
                    hour=p_time.hour, minute=p_time.minute, second=0, microsecond=0
                )
                logger.debug(
                    "Time phrase %r resolved to hour=%d, minute=%d. Final datetime=%s",
                    time_phrase,
                    p_time.hour,
                    p_time.minute,
                    result.isoformat(),
                )
                return result
            else:
                logger.warning(
                    "dateparser could not resolve time phrase %r; falling back to keyword-based heuristic matching.",
                    time_phrase,
                )
                if "morning" in clean_time or "matin" in clean_time:
                    result = p_date.replace(hour=8, minute=0, second=0, microsecond=0)
                    logger.debug(
                        "Matched 'morning/matin' keyword; defaulting to 08:00. Result=%s",
                        result.isoformat(),
                    )
                    return result
                elif "afternoon" in clean_time or "après-midi" in clean_time:
                    result = p_date.replace(hour=13, minute=0, second=0, microsecond=0)
                    logger.debug(
                        "Matched 'afternoon/après-midi' keyword; defaulting to 13:00. Result=%s",
                        result.isoformat(),
                    )
                    return result
                elif "evening" in clean_time or "soir" in clean_time:
                    result = p_date.replace(hour=18, minute=0, second=0, microsecond=0)
                    logger.debug(
                        "Matched 'evening/soir' keyword; defaulting to 18:00. Result=%s",
                        result.isoformat(),
                    )
                    return result
                else:
                    logger.error(
                        "No keyword heuristic matched time phrase %r; giving up.",
                        time_phrase,
                    )
                    raise ValueError(
                        f"Python dateparser failed to understand the time phrase: '{time_phrase}'"
                    )
        logger.debug(
            "No explicit time phrase provided; inspecting date phrase %r for time-of-day keywords.",
            date_phrase,
        )
        lower_date = date_phrase.lower()
        if "morning" in lower_date or "matin" in lower_date:
            result = p_date.replace(hour=8, minute=0, second=0, microsecond=0)
            logger.debug(
                "Matched 'morning/matin' keyword in date phrase; defaulting to 08:00. Result=%s",
                result.isoformat(),
            )
            return result
        elif "afternoon" in lower_date or "après-midi" in lower_date:
            result = p_date.replace(hour=13, minute=0, second=0, microsecond=0)
            logger.debug(
                "Matched 'afternoon/après-midi' keyword in date phrase; defaulting to 13:00. Result=%s",
                result.isoformat(),
            )
            return result
        elif "evening" in lower_date or "soir" in lower_date:
            result = p_date.replace(hour=18, minute=0, second=0, microsecond=0)
            logger.debug(
                "Matched 'evening/soir' keyword in date phrase; defaulting to 18:00. Result=%s",
                result.isoformat(),
            )
            return result
        elif prefer_time == "end":
            result = p_date.replace(hour=12, minute=0, second=0, microsecond=0)
            logger.debug(
                "No time keyword found; prefer_time='end', defaulting to 12:00. Result=%s",
                result.isoformat(),
            )
            return result
        else:
            result = p_date.replace(hour=8, minute=0, second=0, microsecond=0)
            logger.debug(
                "No time keyword found; prefer_time='start', defaulting to 08:00. Result=%s",
                result.isoformat(),
            )
            return result

    def execute(self, input_json_path: Path) -> str:
        pipeline_start_time = time.monotonic()
        logger.info(
            "=== SchedulingPipeline.execute() starting for input_json_path=%s ===",
            input_json_path,
        )
        print("[SchedulingPipeline] Executing deterministic extraction...")
        logger.debug("Step 1/6: Running deterministic cleaner on input.")
        current_text = self.apply_deterministic_cleaner(input_json_path)
        logger.debug(
            "Deterministic cleaner produced %d characters of text. Preview: %r",
            len(current_text),
            current_text[:200],
        )
        dict_path_str = self.profile_data.get(
            "dictionary", "configs/hallucinations_dict.yaml"
        )
        dict_path = str(self.repo_root / dict_path_str)
        logger.debug(
            "Step 2/6: Applying regex_replacer using hallucination dictionary at %s",
            dict_path,
        )
        current_text = regex_replacer(current_text, dict_path, strip_markers=True)
        logger.debug(
            "Text after regex_replacer (%d chars). Preview: %r",
            len(current_text),
            current_text[:200],
        )
        logger.debug(
            "Step 3/6: Applying grammar_checker with language=%r", self.language
        )
        current_text = grammar_checker(
            current_text, language=self.language, strip_markers=True
        )
        logger.debug(
            "Text after grammar_checker (%d chars). Preview: %r",
            len(current_text),
            current_text[:200],
        )
        llm_cfg = self.profile_data["post_processing"][0]
        logger.info(
            "Step 4/6: LLM configuration resolved -> provider=%r, model=%r, endpoint=%r",
            llm_cfg.get("provider"),
            llm_cfg.get("model"),
            llm_cfg.get("endpoint"),
        )
        timestamp_str = self.metadata.get("timestamp")
        if timestamp_str:
            logger.debug(
                "Metadata timestamp found: %r. Attempting strptime parse.",
                timestamp_str,
            )
            try:
                base_context_date = datetime.datetime.strptime(
                    timestamp_str, "%Y%m%d_%H%M%S"
                )
                logger.debug(
                    "Parsed base_context_date from metadata: %s",
                    base_context_date.isoformat(),
                )
            except ValueError:
                logger.warning(
                    "Failed to parse metadata timestamp %r with format %%Y%%m%%d_%%H%%M%%S. Falling back to datetime.now().",
                    timestamp_str,
                )
                base_context_date = datetime.datetime.now()
        else:
            logger.debug(
                "No metadata timestamp present. Using datetime.now() as base_context_date."
            )
            base_context_date = datetime.datetime.now()
        logger.info(
            "Base context date for temporal parsing: %s", base_context_date.isoformat()
        )
        max_retries = 3
        logger.debug(
            "Step 5/6: Entering LLM extraction/validation loop (max_retries=%d).",
            max_retries,
        )
        for attempt in range(max_retries):
            attempt_start_time = time.monotonic()
            logger.info(
                "--- LLM extraction attempt %d/%d ---", attempt + 1, max_retries
            )
            logger.debug(
                "Calling self.call_llm() with input_text length=%d chars.",
                len(current_text),
            )
            raw_output = self.call_llm(
                provider_type=llm_cfg["provider"],
                model=llm_cfg["model"],
                prompt_template=llm_cfg["prompt"],
                input_text=current_text,
                endpoint=llm_cfg.get("endpoint"),
                schema=LLMEventExtraction,
            )
            logger.debug(
                "Raw LLM output received (%d chars). Preview: %r",
                len(raw_output) if raw_output else 0,
                raw_output[:300] if raw_output else raw_output,
            )
            try:
                logger.debug(
                    "Validating raw LLM output against LLMEventExtraction schema."
                )
                llm_data = LLMEventExtraction.model_validate_json(raw_output)
                logger.info(
                    "LLM extraction validated successfully: intent_type=%r, title=%r, raw_start_date=%r, raw_start_time=%r, raw_end_time=%r",
                    llm_data.intent_type,
                    llm_data.title,
                    llm_data.raw_start_date,
                    llm_data.raw_start_time,
                    llm_data.raw_end_time,
                )
                print(
                    "[SchedulingPipeline] LLM Extracted Data successfully. Handing off to Python dateparser..."
                )
                logger.debug(
                    "Resolving start datetime via parse_temporal_components()."
                )
                dt_start = self.parse_temporal_components(
                    date_phrase=llm_data.raw_start_date,
                    time_phrase=llm_data.raw_start_time,
                    base_date=base_context_date,
                    prefer_time="start",
                )
                logger.info("Resolved dt_start=%s", dt_start.isoformat())
                if llm_data.raw_end_time:
                    logger.debug(
                        "Explicit raw_end_time=%r present; resolving end datetime via parse_temporal_components().",
                        llm_data.raw_end_time,
                    )
                    dt_end = self.parse_temporal_components(
                        date_phrase=llm_data.raw_start_date,
                        time_phrase=llm_data.raw_end_time,
                        base_date=base_context_date,
                        prefer_time="end",
                    )
                    logger.info(
                        "Resolved dt_end from explicit end time=%s", dt_end.isoformat()
                    )
                else:
                    logger.debug(
                        "No raw_end_time provided; inferring end time via keyword heuristic on combined start date/time phrase."
                    )
                    combined_phrase = f"{llm_data.raw_start_date} {llm_data.raw_start_time or ''}".lower()
                    logger.debug("Combined phrase for heuristic: %r", combined_phrase)
                    if "morning" in combined_phrase or "matin" in combined_phrase:
                        dt_end = dt_start.replace(hour=12, minute=0)
                        logger.debug(
                            "Matched morning heuristic; dt_end set to 12:00 -> %s",
                            dt_end.isoformat(),
                        )
                    elif (
                        "afternoon" in combined_phrase
                        or "après-midi" in combined_phrase
                    ):
                        dt_end = dt_start.replace(hour=15, minute=0)
                        logger.debug(
                            "Matched afternoon heuristic; dt_end set to 15:00 -> %s",
                            dt_end.isoformat(),
                        )
                    elif "evening" in combined_phrase or "soir" in combined_phrase:
                        dt_end = dt_start.replace(hour=19, minute=0)
                        logger.debug(
                            "Matched evening heuristic; dt_end set to 19:00 -> %s",
                            dt_end.isoformat(),
                        )
                    else:
                        dt_end = dt_start + datetime.timedelta(hours=1)
                        logger.debug(
                            "No keyword matched; defaulting dt_end to dt_start + 1 hour -> %s",
                            dt_end.isoformat(),
                        )
                if (
                    dt_end.hour == 0
                    and dt_end.minute == 0
                    and (dt_start.hour >= 6)
                    and (dt_start.hour <= 12)
                ):
                    dt_end = dt_end.replace(hour=12)
                    logger.warning(
                        "Detected likely midnight ambiguity (dt_end=00:00 but dt_start=%s). Adjusting dt_end to 12:00 (noon). New dt_end=%s",
                        dt_start.isoformat(),
                        dt_end.isoformat(),
                    )
                if dt_end < dt_start and dt_end.hour < dt_start.hour:
                    dt_end += datetime.timedelta(days=1)
                    logger.warning(
                        "Detected likely overnight event (dt_end < dt_start). Rolling dt_end forward by 1 day. New dt_end=%s",
                        dt_end.isoformat(),
                    )
                if dt_end < dt_start and dt_start.hour >= 12 and (dt_end.hour < 12):
                    logger.warning(
                        "Detected likely AM/PM ambiguity (dt_end=%s < dt_start=%s, start hour>=12, end hour<12). Adding 12 hours to dt_end to correct.",
                        dt_end.isoformat(),
                        dt_start.isoformat(),
                    )
                    dt_end = dt_end + datetime.timedelta(hours=12)
                    logger.debug("Corrected dt_end=%s", dt_end.isoformat())
                if dt_end < dt_start:
                    logger.error(
                        "Temporal logic failure: dt_end=%s is still before dt_start=%s after correction attempts.",
                        dt_end.isoformat(),
                        dt_start.isoformat(),
                    )
                    raise ValueError(
                        f"Temporal Logic Failure: Calculated end time ({dt_end}) is before the start time ({dt_start})."
                    )
                duration_mins = int((dt_end - dt_start).total_seconds() / 60)
                logger.info(
                    "Computed final schedule: start=%s, end=%s, duration_minutes=%d",
                    dt_start.isoformat(),
                    dt_end.isoformat(),
                    duration_mins,
                )
                logger.debug("Constructing final ScheduleEvent object.")
                final_event = ScheduleEvent(
                    intent_type=llm_data.intent_type,
                    title=llm_data.title,
                    start_datetime=dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    end_datetime=dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                    duration_minutes=duration_mins,
                    location=llm_data.location,
                    description=llm_data.description,
                    recurrence=llm_data.recurrence,
                )
                print(
                    "[SchedulingPipeline] Python Logistic Math finalized successfully."
                )
                elapsed_attempt = time.monotonic() - attempt_start_time
                elapsed_total = time.monotonic() - pipeline_start_time
                logger.info(
                    "Attempt %d succeeded in %.3fs. Total pipeline execution time: %.3fs.",
                    attempt + 1,
                    elapsed_attempt,
                    elapsed_total,
                )
                result_json = final_event.model_dump_json(indent=2)
                logger.debug("Final serialized ScheduleEvent JSON: %s", result_json)
                logger.info(
                    "=== SchedulingPipeline.execute() completed successfully ==="
                )
                return result_json
            except (ValidationError, ValueError) as e:
                elapsed_attempt = time.monotonic() - attempt_start_time
                logger.warning(
                    "Validation/Parsing error on attempt %d/%d after %.3fs: %s",
                    attempt + 1,
                    max_retries,
                    elapsed_attempt,
                    e,
                )
                print(
                    f"⚠️ [SchedulingPipeline] Validation/Parsing Error on attempt {attempt + 1}: {e}"
                )
                if attempt == max_retries - 1:
                    logger.error(
                        "Max validation retries (%d) reached. Aborting and returning error payload. Last raw_output: %r",
                        max_retries,
                        raw_output,
                    )
                    print(
                        "❌ [SchedulingPipeline] Max validation retries reached. Returning error payload."
                    )
                    return json.dumps(
                        {
                            "error": f"Validation failed after {max_retries} attempts",
                            "raw_output": raw_output,
                        }
                    )
                logger.debug(
                    "Appending system error correction note to current_text for retry %d.",
                    attempt + 2,
                )
                current_text += f"\n\n[SYSTEM ERROR IN PREVIOUS PASS]: Your previous JSON output caused a pipeline failure. Correct the following strict logistic errors:\n{str(e)}"
        logger.error(
            "Step 6/6: Exited retry loop without returning (unexpected code path). Returning last raw_output as fallback."
        )
        return raw_output
