"""
Wrapper around `py3-pinterest` (https://github.com/bstoilov/py3-pinterest) —
an actively-maintained, fully-fledged Pinterest client (v2.0.0, explicitly
fixed "Error in search" as of their April 2026 release, which is exactly
the bug we were hitting with the previous library).

Two lessons baked in after two rounds of production failures against real
Pinterest responses:

1. Field names are NOT trustworthy across queries/versions - Pinterest's
   internal API shape varies (and this library returns it close to raw).
   So instead of reading fixed keys like raw["images"]["236x"]["url"],
   we recursively scan the *entire* raw pin payload for anything that
   looks like a Pinterest CDN image/video URL. This is schema-agnostic:
   as long as Pinterest's CDN domains (i.pinimg.com / v1.pinimg.com) don't
   change, this keeps working even if the wrapping dict structure does.
2. search() is paginated via a `bookmark` - we loop until we have enough
   *usable* (non-empty) results instead of trusting a single page.
"""
import logging
import re
from typing import Optional

from py3pin.Pinterest import Pinterest as _Py3PinClient

logger = logging.getLogger(__name__)

_IMG_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9\-.]*pinimg\.com/[^\s"\'\\]+\.(?:jpg|jpeg|png|gif|webp)', re.IGNORECASE
)
_VIDEO_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9\-.]*pinimg\.com/[^\s"\'\\]+\.mp4', re.IGNORECASE
)

# Pinterest encodes size in the URL path, e.g. .../736x/... or .../originals/...
_ORIGINAL_HINTS = ("/originals/",)
_PREVIEW_HINTS = ("/736x/", "/564x/", "/474x/")
_THUMB_HINTS = ("/236x/", "/170x/", "/135x136/", "/60x60/")


def _collect_urls(obj, pattern: re.Pattern, out: list) -> None:
    if isinstance(obj, str):
        out.extend(pattern.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_urls(v, pattern, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, pattern, out)


def _pick(urls: list, hints: tuple) -> Optional[str]:
    for hint in hints:
        for u in urls:
            if hint in u:
                return u
    return None


def _first_str(raw: dict, *keys: str) -> Optional[str]:
    for key in keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


class PinterestMedia:
    """Normalized view of a single Pinterest pin, built by scanning the raw
    payload for media URLs rather than trusting a fixed schema."""

    def __init__(self, raw: dict):
        self.id: str = str(raw.get("id") or raw.get("pin_id") or "")

        raw_url = raw.get("url")
        if isinstance(raw_url, str) and "/pin/" in raw_url:
            self.pin_url = raw_url
        elif self.id:
            self.pin_url = f"https://www.pinterest.com/pin/{self.id}/"
        else:
            self.pin_url = "https://www.pinterest.com"

        self.title: str = (
            _first_str(raw, "title", "grid_title", "description", "auto_alt_text", "seo_alt_text") or ""
        )

        images: list = []
        videos: list = []
        _collect_urls(raw, _IMG_URL_RE, images)
        _collect_urls(raw, _VIDEO_URL_RE, videos)
        images = list(dict.fromkeys(images))  # dedupe, keep order
        videos = list(dict.fromkeys(videos))

        self.original_url = _pick(images, _ORIGINAL_HINTS) or _pick(images, _PREVIEW_HINTS) or (
            images[0] if images else None
        )
        self.preview_url = _pick(images, _PREVIEW_HINTS) or self.original_url
        self.thumb_url = _pick(images, _THUMB_HINTS) or self.preview_url

        self.video_url: Optional[str] = videos[0] if videos else None
        self.video_poster: Optional[str] = self.preview_url or self.thumb_url

        self.media_type = "video" if self.video_url else "image"

    @property
    def is_video(self) -> bool:
        return self.media_type == "video" and bool(self.video_url)

    @property
    def has_media(self) -> bool:
        return bool(self.thumb_url or self.preview_url or self.original_url or self.video_url)

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

    @classmethod
    def from_dict(cls, data: dict) -> "PinterestMedia":
        obj = cls.__new__(cls)
        obj.id = data.get("id", "")
        obj.pin_url = data.get("pin_url", "https://www.pinterest.com")
        obj.title = data.get("title", "")
        obj.media_type = data.get("media_type", "image")
        obj.thumb_url = data.get("thumb_url")
        obj.preview_url = data.get("preview_url")
        obj.original_url = data.get("original_url")
        obj.video_url = data.get("video_url")
        obj.video_poster = data.get("video_poster")
        return obj


class PinterestService:
    def __init__(self, email: str = "", password: str = "", username: str = "", cred_root: str = "/tmp/pinterest_cred"):
        # email/password/username are optional — search works anonymously.
        # They're only used if the owner-only /login command is triggered.
        self._client = _Py3PinClient(
            email=email or None,
            password=password or None,
            username=username or None,
            cred_root=cred_root,
        )
        self._logged_in = False

    def login(self) -> bool:
        """Blocking (Selenium). Call via asyncio.to_thread. Requires Chrome
        to be installed in the container — see README."""
        try:
            self._client.login()
            self._logged_in = True
            return True
        except Exception:
            logger.exception("py3-pinterest login failed.")
            return False

    def search(self, query: str, limit: int = 15, max_pages: int = 5) -> list[PinterestMedia]:
        """Blocking. Call via asyncio.to_thread from async code."""
        results: list[PinterestMedia] = []
        seen_ids: set = set()

        try:
            batch = self._client.search(scope="pins", query=query, reset_bookmark=True)
        except Exception:
            logger.exception("Pinterest search request raised for query=%r", query)
            return []

        pages = 0
        while batch and len(results) < limit and pages < max_pages:
            if pages == 0 and batch:
                logger.info("First raw pin keys for query=%r: %s", query, list(batch[0].keys()))

            for raw in batch:
                media = PinterestMedia(raw)
                if not media.has_media:
                    continue
                if media.id and media.id in seen_ids:
                    continue
                seen_ids.add(media.id)
                results.append(media)
                if len(results) >= limit:
                    break

            pages += 1
            if len(results) >= limit:
                break

            try:
                batch = self._client.search(scope="pins", query=query)
            except Exception:
                logger.exception("Pinterest search pagination raised for query=%r (page %d)", query, pages)
                break

        if not results:
            logger.warning("No usable results resolved for query=%r after %d page(s).", query, pages)

        return results
