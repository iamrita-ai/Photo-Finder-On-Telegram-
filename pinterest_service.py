"""
Wrapper around the `pinterest-downloader` library
(https://github.com/x7007x/PinterestDownloader) which scrapes Pinterest's
public, unauthenticated endpoints - no API key or login needed for search.

All calls into the underlying library are synchronous (blocking `requests`
calls), so callers in bot.py must run them via `asyncio.to_thread(...)`
to avoid blocking the bot's event loop.
"""
import logging
from typing import Optional

from pinterest_downloader import Pinterest

logger = logging.getLogger(__name__)


class PinterestMedia:
    """Normalized view of a single Pinterest pin, safe to store as plain dict."""

    def __init__(self, raw: dict):
        self.id: str = str(raw.get("id", ""))
        self.title: str = (raw.get("title") or raw.get("description") or "").strip()
        self.pin_url: str = raw.get("url") or f"https://www.pinterest.com/pin/{self.id}/"
        self.media_type: str = raw.get("media_type", "image")

        images = raw.get("images") or {}
        self.thumb_url: Optional[str] = _first_url(images, "236x", "170x", "474x")
        self.preview_url: Optional[str] = _first_url(images, "736x", "474x", "236x")
        self.original_url: Optional[str] = _first_url(images, "orig", "736x", "474x")

        self.video_url: Optional[str] = None
        self.video_poster: Optional[str] = None
        if self.media_type == "video":
            video = raw.get("video") or {}
            formats = [f for f in (video.get("formats") or []) if f.get("url", "").endswith(".mp4")]
            best = max(formats, key=lambda f: f.get("width", 0), default=None)
            if best:
                self.video_url = best.get("url")
            self.video_poster = video.get("poster") or self.preview_url

    @property
    def is_video(self) -> bool:
        return self.media_type == "video" and bool(self.video_url)

    @property
    def is_gif(self) -> bool:
        return self.media_type == "gif"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "pin_url": self.pin_url,
            "media_type": self.media_type,
            "thumb_url": self.thumb_url,
            "preview_url": self.preview_url,
            "original_url": self.original_url,
            "video_url": self.video_url,
            "video_poster": self.video_poster,
        }


def _first_url(images: dict, *keys: str) -> Optional[str]:
    for key in keys:
        entry = images.get(key)
        if entry and entry.get("url"):
            return entry["url"]
    return None


class PinterestService:
    def __init__(self):
        self._client = Pinterest()

    def search(self, query: str, limit: int = 10) -> list[PinterestMedia]:
        """Blocking. Call via asyncio.to_thread from async code."""
        try:
            result = self._client.search(query, page_size=min(max(limit, 1), 25))
        except Exception:
            logger.exception("Pinterest search request raised for query=%r", query)
            return []

        if not result.get("ok"):
            logger.warning("Pinterest search failed for %r: %s", query, result.get("error"))
            return []

        pins = result.get("pins") or []
        return [PinterestMedia(p) for p in pins[:limit]]

    def get_pin(self, pin_url_or_id: str) -> Optional[PinterestMedia]:
        """Blocking. Call via asyncio.to_thread from async code."""
        try:
            result = self._client.get_pin(pin_url_or_id)
        except Exception:
            logger.exception("Pinterest get_pin raised for %r", pin_url_or_id)
            return None

        if not result.get("ok"):
            logger.warning("Pinterest get_pin failed for %r: %s", pin_url_or_id, result.get("error"))
            return None
        return PinterestMedia(result["pin"])
