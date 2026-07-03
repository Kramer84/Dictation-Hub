import sys
import json
import unittest
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Configure runpaths dynamically without packaging constraints
repo_root = Path(__file__).resolve().parent.parent
sys.path.append(str(repo_root))
sys.path.append(str(repo_root / "post_processing"))

from post_processing.pipelines.scheduling import SchedulingPipeline


class TestLiveSchedulingPipeline(unittest.TestCase):
    def setUp(self):
        self.static_config = MagicMock()
        self.static_config.suffixes.full_json = "_full.json"
        self.static_config.suffixes.cleaned_json = "_cleaned.json"
        
        # Target the active local model from your profile configurations
        self.profile_data = {
            "dictionary": "configs/hallucinations_dict.yaml",
            "post_processing": [
                {
                    "provider": "local",
                    "model": "Osmosis/Osmosis-Structure-0.6B:latest",
                    "endpoint": "http://localhost:11434/v1/chat/completions",
                    "prompt": (
                        "You are a specialized calendar data extraction tool. Extract schedule details from the raw text. "
                        "Populate the fields exactly based on the structural definitions provided."
                    )
                }
            ]
        }
        self.workspace_dir = repo_root / "tests"
        self.user_info = {
            "location": "Clermont-Ferrand, Auvergne-Rhône-Alpes, France",
            "profession": "PhD Student in Engineering"
        }

    @patch('post_processing.pipelines.base.datetime')
    @patch('post_processing.pipelines.scheduling.datetime')
    @patch.object(SchedulingPipeline, 'apply_deterministic_cleaner')
    def test_live_llm_extraction_and_reconstruction(self, mock_cleaner, mock_sched_dt, mock_base_dt):
        """
        Executes live queries against the local Ollama daemon while freezing the 
        internal system pipeline clock to Friday, July 3, 2026.
        """
        # 1. Freeze the temporal environment across both pipelines
        frozen_now = datetime.datetime(2026, 7, 3, 16, 10, 0)
        
        mock_sched_dt.datetime.now.return_value = frozen_now
        mock_sched_dt.datetime.strptime = datetime.datetime.strptime
        mock_sched_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        mock_sched_dt.timedelta = datetime.timedelta

        mock_base_dt.datetime.now.return_value = frozen_now
        mock_base_dt.datetime.strptime = datetime.datetime.strptime
        mock_base_dt.timedelta = datetime.timedelta

        # 2. Build out real multi-lingual verification cases
        test_cases = [
            {
                "name": "English Relative Range",
                "dictated_text": "I have a meeting next Tuesday between 1.30pm and 3.30pm for a job interview with Michelin",
                "expected_start": "2026-07-14T13:30:00",
                "expected_end": "2026-07-14T15:30:00",
                "expected_duration": 120,
                "expected_intent": "single_event",
                "expected_title_keyword": "michelin"
            },
            {
                "name": "French Specific Time Range",
                "dictated_text": "J'ai une réunion mardi prochain entre 13h30 et 15h30 pour un entretien chez Michelin.",
                "expected_start": "2026-07-14T13:30:00",
                "expected_end": "2026-07-14T15:30:00",
                "expected_duration": 120,
                "expected_intent": "single_event",
                "expected_title_keyword": "michelin"
            },
            {
                "name": "French Recurring Structural Block",
                "dictated_text": "Réserve-moi tous les lundis matins pendant les 4 prochaines semaines pour une soutenance blanche chaque matin pour ma thèse.",
                "expected_start": "2026-07-06T08:00:00",
                "expected_end": "2026-07-06T12:00:00",
                "expected_duration": 240,
                "expected_intent": "recurring_event",
                "expected_title_keyword": "soutenance"
            }
        ]

        # 3. Instantiate the operational pipeline architecture
        pipeline = SchedulingPipeline(
            repo_root=repo_root,
            static_config=self.static_config,
            profile_data=self.profile_data,
            workspace_dir=self.workspace_dir,
            user_information=self.user_info
        )
        
        # Override metadata clock property to align base calculations
        pipeline.metadata = {
            "language": "en",
            "timestamp": "20260703_161000"
        }
        pipeline.language = "en"

        mock_dummy_json = self.workspace_dir / "live_test_run_full.json"
        mock_dummy_json.touch(exist_ok=True)

        try:
            for case in test_cases:
                print(f"\n🚀 [LIVE LLM TEST RUN]: {case['name']}")
                
                # Intercept the audio reading phase and stream our explicit test string instead
                mock_cleaner.return_value = case["dictated_text"]

                # Fire execution directly into your local Ollama daemon server
                output_payload_str = pipeline.execute(mock_dummy_json)
                print(f"└── Raw Output Received:\n{output_payload_str}")
                
                # Parse output payload to verify correct execution
                result = json.loads(output_payload_str)
                
                # Assertions checking structural and mathematical alignment
                self.assertEqual(result["intent_type"], case["expected_intent"])
                self.assertEqual(result["start_datetime"], case["expected_start"])
                self.assertEqual(result["end_datetime"], case["expected_end"])
                self.assertEqual(result["duration_minutes"], case["expected_duration"])
                
                # Dynamic title validation based on the specific test case
                self.assertIn(
                    case["expected_title_keyword"], 
                    result["title"].lower() if result["title"] else "",
                    f"Expected keyword '{case['expected_title_keyword']}' not found in title '{result['title']}'"
                )

            print("\n✅ Verification complete: All live local iterations executed successfully.")

        finally:
            if mock_dummy_json.exists():
                mock_dummy_json.unlink()


if __name__ == "__main__":
    unittest.main()