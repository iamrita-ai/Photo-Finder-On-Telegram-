"""
Fetches Pinterest pin comments via the Apify "Pinterest Comments Scraper"
actor (https://apify.com/easyapi/pinterest-comments-scraper), used as the
primary comments source since Pinterest's own internal comment endpoint
(used by py3-pinterest) currently returns 404 upstream.

This is a PAID third-party service — each call consumes your Apify
account's credits. Requires the APIFY_API_TOKEN env var; if unset, this
is skipped entirely and bot.py falls back to the (best-effort, often
broken) py3-pinterest comments method.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_ACTOR_ID = "easyapi/pinterest-comments-scraper"


def fetch_comments(api_token: str, pin_url: str, limit: int = 10) -> list:
    """Blocking (network call). Call via asyncio.to_thread. Returns a list
    of comment strings, or [] on failure/misconfiguration."""
    if not api_token:
        return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.error("apify-client not installed — check requirements.txt")
        return []

    try:
        client = ApifyClient(api_token)
        run = client.actor(_ACTOR_ID).call(run_input={"pinUrl": pin_url, "limit": limit})

        # apify-client's run object supports both forms depending on version.
        dataset_id = getattr(run, "default_dataset_id", None) or run["defaultDatasetId"]

        texts = []
        for item in client.dataset(dataset_id).iterate_items():
            text = _first_text(item)
            if text:
                texts.append(text)
            if len(texts) >= limit:
                break
        return texts
    except Exception:
        logger.exception("Apify comment fetch failed for %s", pin_url)
        return []


def _first_text(item: dict) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("text", "comment", "commentText", "body", "message"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None
