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
        self.title: str = (raw.get("title") or raw.get("description") or raw.get("alt") or "").strip()
        self.pin_url: str = raw.get("url") or (
            f"https://www.pinterest.com/pin/{self.id}/" if self.id else "https://www.pinterest.com"
        )
        self.media_type: str = raw.get("media_type", "image")

        images = raw.get("images") or {}
        self.thumb_url: Optional[str] = _first_url(images, "236x", "170x", "474x") or _fallback_image_url(raw)
        self.preview_url: Optional[str] = (
            _first_url(images, "736x", "474x", "236x") or _fallback_image_url(raw)
        )
        self.original_url: Optional[str] = (
            _first_url(images, "orig", "736x", "474x") or _fallback_image_url(raw)
        )

        self.video_url: Optional[str] = None
        self.video_poster: Optional[str] = None
        if self.media_type == "video":
            video = raw.get("video") or {}
            formats = [f for f in (video.get("formats") or []) if _looks_like_mp4(f.get("url"))]
            best = max(formats, key=lambda f: f.get("width", 0), default=None)
            if best:
                self.video_url = best.get("url")
            elif video.get("formats"):
                # No .mp4 match found (e.g. only HLS) — fall back to the
                # highest-res format anyway; Telegram can sometimes still
                # play it, and if not, download_callback/_send_pin handle
                # the "no url" case gracefully.
                best_any = max(video["formats"], key=lambda f: f.get("width", 0), default=None)
                self.video_url = (best_any or {}).get("url")
            self.video_poster = video.get("poster") or self.preview_url or self.thumb_url

        if not (self.thumb_url or self.preview_url or self.original_url or self.video_url):
            logger.warning(
                "No usable media URL found for pin id=%s media_type=%s — raw keys: %s",
                self.id,
                self.media_type,
                list(raw.keys()),
            )
            logger.debug("Full raw pin payload for id=%s: %s", self.id, raw)

    @property
    def is_video(self) -> bool:
        return self.media_type == "video" and bool(self.video_url)

    @property
    def is_gif(self) -> bool:
        return self.media_type == "gif"

    @property
    def needs_resolve(self) -> bool:
        """True when this pin came from search() as a lightweight stub
        (id/title/url/media_type only) and still needs get_pin() to fetch
        actual image/video URLs."""
        return not (self.thumb_url or self.preview_url or self.original_url or self.video_url)

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
    """Handles both {"236x": {"url": "..."}} and {"236x": "https://..."} shapes."""
    for key in keys:
        entry = images.get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
        if isinstance(entry, str) and entry:
            return entry
    return None


def _fallback_image_url(raw: dict) -> Optional[str]:
    """Covers alternate/older response shapes seen in the wild for this library."""
    for key in ("image_url", "thumbnail", "thumbnail_url", "src", "cover_url", "photo_url"):
        val = raw.get(key)
        if isinstance(val, str) and val:
            return val

    media = raw.get("media")
    if isinstance(media, dict):
        for key in ("url", "poster"):
            val = media.get(key)
            if isinstance(val, str) and val:
                return val

    embed = raw.get("embed")
    if isinstance(embed, dict) and isinstance(embed.get("src"), str):
        return embed["src"]

    return None


def _looks_like_mp4(url: Optional[str]) -> bool:
    return bool(url) and ".mp4" in url.lower()


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
        if pins:
            logger.info("First raw pin keys for query=%r: %s", query, list(pins[0].keys()))
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

    def resolve(self, pin: PinterestMedia) -> PinterestMedia:
        """Blocking. Call via asyncio.to_thread from async code.

        search() only returns a lightweight stub (id/title/url/media_type) —
        no image or video URLs. This fetches the full pin via get_pin() so it
        can actually be sent to Telegram. If `pin` already has media URLs
        (e.g. it was already resolved once and cached), this is a no-op.
        """
        if not pin.needs_resolve:
            return pin

        full = self.get_pin(pin.pin_url or pin.id)
        if full is None:
            return pin
        if not full.title:
            full.title = pin.title
        return full
