import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..server.n8n_dispatcher import push_to_n8n

logger = logging.getLogger(__name__)


@dataclass
class PostProcessResult:
    raw_text: str
    final_text: str
    error: Optional[str] = None


def run_post_processing(
    pipeline, json_path: Path, workspace: Path, timestamp: str, suffixes
) -> PostProcessResult:
    """Run a profile pipeline, degrading to the scrubbed text if it fails.

    The scrubbed text (``<ts>_raw.txt``) is written by the deterministic
    cleaner before any LLM call, so it survives LLM errors and timeouts.
    """
    raw_txt_path = workspace / f"{timestamp}{suffixes.raw_text}"
    error = None
    try:
        final_text = pipeline.execute(json_path)
    except Exception as e:
        # The caller reports the failure; the traceback is for the session log.
        logger.info("Post-processing failed; falling back to scrubbed text.", exc_info=True)
        error = f"{type(e).__name__}: {e}"
        final_text = None
    raw_text = (
        raw_txt_path.read_text(encoding="utf-8").strip() if raw_txt_path.exists() else ""
    )
    if final_text is None:
        final_text = raw_text
    (workspace / f"{timestamp}{suffixes.final_text}").write_text(final_text, encoding="utf-8")
    return PostProcessResult(raw_text=raw_text, final_text=final_text, error=error)


def dispatch_webhook(profile_data: dict, result: PostProcessResult, workspace: Path) -> None:
    """Push the final payload to the profile's webhook, never an error fallback."""
    webhook_url = profile_data.get("webhook_url")
    if not webhook_url:
        return
    if result.error:
        logger.warning("Skipping webhook: post-processing failed (%s).", result.error)
        return
    try:
        payload = json.loads(result.final_text)
    except json.JSONDecodeError:
        payload = {"text_content": result.final_text}
    push_to_n8n(webhook_url, payload, workspace.name)
