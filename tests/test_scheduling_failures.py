from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dictation_hub.pipeline.pipelines import scheduling
from dictation_hub.pipeline.pipelines.scheduling import SchedulingPipeline

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_unusable_llm_output_raises_instead_of_returning_an_error_payload(tmp_path):
    pipeline = SchedulingPipeline(
        repo_root=REPO_ROOT,
        static_config=MagicMock(),
        profile_data={
            "dictionary": "configs/hallucinations_dict.yaml",
            "post_processing": [{"provider": "local", "model": "m", "prompt": "p"}],
        },
        workspace_dir=tmp_path,
    )
    with patch.object(SchedulingPipeline, "apply_deterministic_cleaner", return_value="lunch tomorrow"), \
         patch.object(scheduling, "grammar_checker", side_effect=lambda text, **kw: text), \
         patch.object(SchedulingPipeline, "call_llm", return_value="this is not json"):
        with pytest.raises(scheduling.SchedulingError):
            pipeline.execute(tmp_path / "x_full.json")
