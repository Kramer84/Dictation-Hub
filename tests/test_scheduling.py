import sys
import json
import pytest
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from freezegun import freeze_time

# Configure runpaths dynamically without packaging constraints
repo_root = Path(__file__).resolve().parent.parent
sys.path.append(str(repo_root))
sys.path.append(str(repo_root / "post_processing"))

from post_processing.pipelines.scheduling import SchedulingPipeline

# --- FIXTURES ---

@pytest.fixture
def mock_time_environment():
    """
    Freezes the temporal environment globally to Friday, July 3, 2026.
    Ensures ALL libraries (including dateparser) respect the mocked clock.
    """
    frozen_now = "2026-07-03 16:10:00"
    with freeze_time(frozen_now):
        # Return the datetime object just in case the test relies on it
        yield datetime.datetime.strptime(frozen_now, "%Y-%m-%d %H:%M:%S")

@pytest.fixture
def scheduling_pipeline():
    """
    Instantiates the operational pipeline architecture.
    """
    static_config = MagicMock()
    static_config.suffixes.full_json = "_full.json"
    static_config.suffixes.cleaned_json = "_cleaned.json"
    
    # Target the active local model from your profile configurations
    profile_data = {
        "dictionary": "configs/hallucinations_dict.yaml",
        "post_processing": [
            {
                "provider": "local",
                "model": "qwen2.5-coder:7b-instruct",
                "endpoint": "http://localhost:11434/v1/chat/completions",
                "prompt": (
                    "You are a specialized calendar data extraction tool. Extract schedule details from the raw text. Use the CURRENT_CONTEXT metadata provided to anchor relative dates like 'tomorrow', 'this afternoon', or named months.\n\nTarget ISO language: {language}."
                )
            }
        ]
    }
    
    workspace_dir = repo_root / "tests"
    user_info = {
        "location": "Clermont-Ferrand, Auvergne-Rhône-Alpes, France",
        "profession": "PhD Student in Engineering"
    }
    
    pipeline = SchedulingPipeline(
        repo_root=repo_root,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=workspace_dir,
        user_information=user_info
    )
    
    # Override metadata clock property to align base calculations
    pipeline.metadata = {
        "language": "en",
        "timestamp": "20260703_161000"
    }
    pipeline.language = "en"
    
    return pipeline


# --- PARAMETRIZED TEST CASES ---
# Format: (Test Name, Dictated Text, Expected Start, Expected End, Expected Duration, Intent Type, Title Keyword)

test_cases = [
    {
        "name": "English Relative Range",
        "dictated_text": "I have a meeting next Tuesday between 1.30pm and 3.30pm for a job interview with Michelin",
        "language": "en",
        "expected_start": "2026-07-07T13:30:00",
        "expected_end": "2026-07-07T15:30:00",
        "expected_duration": 120,
        "expected_intent": "single_event",
        "expected_title_keyword": "michelin",
        "expected_recurrence": None
    },
    {
        "name": "French Specific Time Range",
        "dictated_text": "J'ai une réunion mardi prochain entre 13h30 et 15h30 pour un entretien chez Michelin.",
        "language": "fr",
        "expected_start": "2026-07-07T13:30:00",
        "expected_end": "2026-07-07T15:30:00",
        "expected_duration": 120,
        "expected_intent": "single_event",
        "expected_title_keyword": "michelin",
        "expected_recurrence": None
    },
    {
        "name": "French Recurring Structural Block",
        "dictated_text": "Réserve-moi tous les lundis matins pendant les 4 prochaines semaines pour une soutenance blanche chaque matin pour ma thèse.",
        "language": "fr",
        "expected_start": "2026-07-06T08:00:00",
        "expected_end": "2026-07-06T12:00:00",
        "expected_duration": 240,
        "expected_intent": "recurring_event",
        "expected_title_keyword": "soutenance",
        "expected_recurrence": {
            "frequency": "WEEKLY",
            "interval": 1,
            "by_day": ["MO"],
            "count": 4,
            "until": None
        }
    },
    {
        "name": "EN Recurring Boxing",
        "dictated_text": "I will have a recurring event Tuesday and Thursday evening between 6.30pm and 8pm for 6 months starting next week for a boxing training",
        "language": "en",
        "expected_start": "2026-07-07T18:30:00",
        "expected_end": "2026-07-07T20:00:00",
        "expected_duration": 90,
        "expected_intent": "recurring_event",
        "expected_title_keyword": "boxing",
        "expected_recurrence": {
            "frequency": "WEEKLY",
            "interval": 1,
            "by_day": ["TU", "TH"],
            "count": None,
            "until": "2027-01-07T20:00:00" # Approximate 6 months
        }
    },
    {
        "name": "EN Relative Days Forward",
        "dictated_text": "In two days I will have a meeting with my head teacher at 5pm to talk about the progress of my work",
        "language": "en",
        "expected_start": "2026-07-05T17:00:00",
        "expected_end": "2026-07-05T18:00:00",
        "expected_duration": 60,
        "expected_intent": "single_event",
        "expected_title_keyword": "progress",
        "expected_recurrence": None
    },
    {
        "name": "FR Thesis Meeting",
        "dictated_text": "J'ai un rendez-vous mardi prochain à 14h avec Pierre Beaurepaire pour parler de ma thèse.",
        "language": "fr",
        "expected_start": "2026-07-07T14:00:00",
        "expected_end": "2026-07-07T15:00:00",
        "expected_duration": 60,
        "expected_intent": "single_event",
        "expected_title_keyword": "beaurepaire",
        "expected_recurrence": None
    },
    {
        "name": "EN Supervisor Meeting",
        "dictated_text": "I will have a meeting next week on Tuesday at 2 p.m. with my supervisor Pierre Beaurepaire",
        "language": "en",
        "expected_start": "2026-07-07T14:00:00",
        "expected_end": "2026-07-07T15:00:00",
        "expected_duration": 60,
        "expected_intent": "single_event",
        "expected_title_keyword": "beaurepaire",
        "expected_recurrence": None
    },
    {
        "name": "EN Broad Time Block",
        "dictated_text": "Next Monday between 8am and 12am there will be a repairman that will work on my heat installation",
        "language": "en",
        "expected_start": "2026-07-06T08:00:00",
        "expected_end": "2026-07-06T12:00:00",
        "expected_duration": 240,
        "expected_intent": "single_event",
        "expected_title_keyword": "repairman",
        "expected_recurrence": None
    }
]

@pytest.mark.parametrize("case", test_cases)
def test_live_llm_extraction_and_reconstruction(case, scheduling_pipeline, mock_time_environment, tmp_path):
    """
    Executes live queries against the local Ollama daemon for individual cases.
    """
    print(f"\n🚀 [LIVE LLM TEST RUN]: {case['name']} ({case['language']})")
    
    # Dynamically set the language for this specific run
    scheduling_pipeline.language = case['language']
    scheduling_pipeline.metadata["language"] = case['language']
    
    with patch.object(SchedulingPipeline, 'apply_deterministic_cleaner', return_value=case['dictated_text']):
        mock_dummy_json = tmp_path / "live_test_run_full.json"
        mock_dummy_json.touch()
        
        output_payload_str = scheduling_pipeline.execute(mock_dummy_json)
        print(f"└── Raw Output Received:\n{output_payload_str}")
        
        result = json.loads(output_payload_str)
        
        # 1. Structural Assertions
        assert result.get("intent_type") == case['expected_intent']
        assert result.get("start_datetime") == case['expected_start']
        assert result.get("end_datetime") == case['expected_end']
        assert result.get("duration_minutes") == case['expected_duration']
        
        # 2. Title Validation
        search_text = f"{result.get('title') or ''} {result.get('description') or ''} {result.get('location') or ''}".lower()
        assert case['expected_title_keyword'] in search_text
        
        # 3. Recurrence Payload Validation
        result_rec = result.get("recurrence")
        expected_rec = case['expected_recurrence']
        
        if expected_rec is None:
            assert result_rec is None, f"Expected no recurrence, but got {result_rec}"
        else:
            assert result_rec is not None, "Expected recurrence payload but got None"
            assert result_rec.get("frequency") == expected_rec.get("frequency")
            assert result_rec.get("interval") == expected_rec.get("interval")
            assert set(result_rec.get("by_day", [])) == set(expected_rec.get("by_day", []))
            assert result_rec.get("count") == expected_rec.get("count")
            
            # Optionally assert 'until' if strictly defined, though LLM variance here will be high
            if expected_rec.get("until"):
                assert result_rec.get("until") is not None