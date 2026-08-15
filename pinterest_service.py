"""
Wrapper around `py3-pinterest` (https://github.com/bstoilov/py3-pinterest).

Design notes (learned the hard way against real Pinterest responses):

1. Field names are NOT trustworthy across queries/versions - Pinterest's
   internal API shape varies. So instead of reading fixed keys, we
   recursively scan the *entire* raw pin payload for anything that looks
   like a Pinterest CDN image/video URL, or a stat with a recognizable key
   name. This is schema-agnostic and survives Pinterest changing the
   wrapping dict structure.
2. search() is paginated via an internal bookmark - we loop until we have
   enough *usable* (non-empty) results instead of trusting a single page.
   search_more() continues that same pagination (for "load more" / infinite
   scroll), and search_random() rotates through topics for the /start
   "explore" feed.
3. Most Pinterest video pins only expose HLS (.m3u8), not a direct .mp4.
   When multiple HLS/mp4 variants exist, we prefer the one with the
   highest resolution hint in its URL, since picking the wrong variant is
   what caused low-quality video delivery.
"""
import logging
import random
import re
from typing import Optional

from py3pin.Pinterest import Pinterest as _Py3PinClient

logger = logging.getLogger(__name__)

_IMG_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9\-.]*pinimg\.com/[^\s"\'\\]+\.(?:jpg|jpeg|png|gif|webp)', re.IGNORECASE
)
_VIDEO_MP4_RE = re.compile(
    r'https?://[a-zA-Z0-9\-.]*pinimg\.com/[^\s"\'\\]+\.mp4', re.IGNORECASE
)
# Most Pinterest video pins only expose an HLS master/variant playlist
# (.m3u8) - not always on a pinimg.com domain, so don't restrict the host.
_VIDEO_HLS_RE = re.compile(
    r'https?://[^\s"\'\\]+\.m3u8[^\s"\'\\]*', re.IGNORECASE
)
_RESOLUTION_HINT_RE = re.compile(r'(\d{3,4})p\b', re.IGNORECASE)

_ORIGINAL_HINTS = ("/originals/",)
_PREVIEW_HINTS = ("/736x/", "/564x/", "/474x/")
_THUMB_HINTS = ("/236x/", "/170x/", "/135x136/", "/60x60/")

# Broad, varied topics used to build a non-repeating "explore" feed for /start.
RANDOM_TOPICS = [
    "trending", "aesthetic wallpaper", "photography", "digital art", "nature",
    "travel destinations", "food recipes", "fashion outfits", "motivational quotes",
    "cars", "anime art", "architecture", "cute dogs", "cute cats", "fitness",
    "diy crafts", "space astronomy", "flowers", "sunset photography", "ocean",
    "mountains landscape", "city skyline", "vintage aesthetic", "minimalist design",
    "street photography", "abstract art", "interior design", "makeup looks",
    "tattoo ideas", "wedding ideas",
]


def _collect_urls(obj, pattern: re.Pattern, out: list) -> None:
    if isinstance(obj, str):
        out.extend(pattern.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_urls(v, pattern, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, pattern, out)


def _pick_by_hint(urls: list, hints: tuple) -> Optional[str]:
    for hint in hints:
        for u in urls:
            if hint in u:
                return u
    return None


def _pick_best_video(urls: list) -> Optional[str]:
    """Prefer the URL with the highest resolution number in its path
    (e.g. '720p'). Falls back to preferring newer HLS versions, then the
    first match — better than picking an arbitrary low-quality variant."""
    if not urls:
        return None

    def score(u: str) -> int:
        m = _RESOLUTION_HINT_RE.search(u)
        if m:
            return int(m.group(1))
        lu = u.lower()
        if "hlsv4" in lu:
            return 500
        if "hlsv3" in lu:
            return 400
        return 0

    return max(urls, key=score)


def _first_str(raw: dict, *keys: str) -> Optional[str]:
    for key in keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _find_number(obj, *key_patterns: str):
    """Recursively find the first numeric value whose key contains any of
    the given (lowercase) substrings."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                kl = k.lower()
                if any(p in kl for p in key_patterns):
                    return int(v)
        for v in obj.values():
            result = _find_number(v, *key_patterns)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_number(item, *key_patterns)
            if result is not None:
                return result
    return None


class PinterestMedia:
    """Normalized view of a single Pinterest pin, built by scanning the raw
    payload for media URLs and stats rather than trusting a fixed schema."""

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
        mp4s: list = []
        hls: list = []
        _collect_urls(raw, _IMG_URL_RE, images)
        _collect_urls(raw, _VIDEO_MP4_RE, mp4s)
        _collect_urls(raw, _VIDEO_HLS_RE, hls)
        images = list(dict.fromkeys(images))
        mp4s = list(dict.fromkeys(mp4s))
        hls = list(dict.fromkeys(hls))

        self.original_url = _pick_by_hint(images, _ORIGINAL_HINTS) or _pick_by_hint(images, _PREVIEW_HINTS) or (
            images[0] if images else None
        )
        self.preview_url = _pick_by_hint(images, _PREVIEW_HINTS) or self.original_url
        self.thumb_url = _pick_by_hint(images, _THUMB_HINTS) or self.preview_url

        self.video_url: Optional[str] = _pick_best_video(mp4s)
        self.video_hls_url: Optional[str] = _pick_best_video(hls)
        self.video_poster: Optional[str] = self.preview_url or self.thumb_url

        self.media_type = "video" if (self.video_url or self.video_hls_url) else "image"

        # Engagement stats - best-effort, Pinterest doesn't expose all of
        # these consistently. `view_count` in particular is rarely present.
        self.like_count = None
        reaction_counts = raw.get("reaction_counts")
        if isinstance(reaction_counts, dict):
            nums = [v for v in reaction_counts.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if nums:
                self.like_count = int(sum(nums))
        if self.like_count is None:
            self.like_count = _find_number(raw, "like_count", "reaction_count")

        self.comment_count = _find_number(raw, "comment_count")
        self.save_count = _find_number(raw, "save_count", "repin_count")
        self.view_count = _find_number(raw, "view_count", "impression_count")

    @property
    def is_video(self) -> bool:
        return self.media_type == "video" and bool(self.video_url or self.video_hls_url)

    @property
    def needs_remux(self) -> bool:
        return bool(self.video_hls_url and not self.video_url)

    @property
    def has_media(self) -> bool:
        return bool(self.thumb_url or self.preview_url or self.original_url or self.video_url or self.video_hls_url)

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
            "video_hls_url": self.video_hls_url,
            "video_poster": self.video_poster,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "save_count": self.save_count,
            "view_count": self.view_count,
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
        obj.video_hls_url = data.get("video_hls_url")
        obj.video_poster = data.get("video_poster")
        obj.like_count = data.get("like_count")
        obj.comment_count = data.get("comment_count")
        obj.save_count = data.get("save_count")
        obj.view_count = data.get("view_count")
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

    # -- internal: shared pagination loop --------------------------------
    def _collect(self, query: str, first_batch: list, limit: int, max_pages: int, seen_ids: set) -> list:
        results: list = []
        batch = first_batch
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
                if media.id:
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

    def search(self, query: str, limit: int = 15, max_pages: int = 5) -> list:
        """Blocking. Call via asyncio.to_thread from async code. Fresh
        search starting from page 1."""
        try:
            batch = self._client.search(scope="pins", query=query, reset_bookmark=True)
        except KeyError:
            # py3-pinterest bug: reset_bookmark does `del bookmark_map[primary][secondary]`
            # without checking the key exists first, raising KeyError the first
            # time a given query is searched. A missing bookmark means there was
            # nothing to reset anyway, so just retry without reset_bookmark.
            logger.info("Harmless bookmark-reset KeyError for query=%r — retrying without reset.", query)
            try:
                batch = self._client.search(scope="pins", query=query)
            except Exception:
                logger.exception("Pinterest search retry (post bookmark KeyError) raised for query=%r", query)
                return []
        except Exception:
            logger.exception("Pinterest search request raised for query=%r", query)
            return []

        return self._collect(query, batch, limit, max_pages, seen_ids=set())

    def search_more(self, query: str, exclude_ids: set, limit: int = 15, max_pages: int = 5) -> list:
        """Blocking. Continues pagination for `query` from wherever the
        client's internal bookmark last left off (does NOT reset) — used
        for "load more" / infinite scroll on an existing search session."""
        try:
            batch = self._client.search(scope="pins", query=query)
        except Exception:
            logger.exception("Pinterest search_more raised for query=%r", query)
            return []
        return self._collect(query, batch, limit, max_pages, seen_ids=exclude_ids)

    def search_random(self, exclude_ids: set, limit: int = 10, topics_to_try: int = 5) -> list:
        """Blocking. Fetches a batch of pins from randomly rotated topics
        for a non-repeating /start explore feed."""
        results: list = []
        topics = random.sample(RANDOM_TOPICS, k=min(topics_to_try, len(RANDOM_TOPICS)))
        for topic in topics:
            batch = self.search(topic, limit=limit - len(results), max_pages=2)
            for media in batch:
                if media.id and media.id in exclude_ids:
                    continue
                if media.id:
                    exclude_ids.add(media.id)
                results.append(media)
                if len(results) >= limit:
                    return results
        return results

    def get_comments(self, pin_id: str, limit: int = 10) -> list:
        """Blocking. Best-effort - Pinterest's comment schema isn't
        documented by py3-pinterest, so we scan each comment dict for a
        text-like field."""
        try:
            raw_comments = self._client.get_comments(pin_id=pin_id, reset_bookmark=True)
        except Exception:
            logger.exception("Failed to fetch comments for pin_id=%s", pin_id)
            return []

        texts = []
        for c in (raw_comments or [])[:limit]:
            text = _first_str(c, "text", "comment", "body", "message") if isinstance(c, dict) else None
            if text:
                texts.append(text)
        return texts
