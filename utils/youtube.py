"""
yt-dlp wrapper – async-safe YouTube audio extraction.

Bot-detection bypass strategy (EC2, 2025+):
  1. ios client + cookies  — iOS client uses cookies natively; no PO token needed.
  2. web client + PO token — bgutil-ytdlp-pot-provider sidecar (localhost:4416)

  If YouTube returns only DRM/SABR formats (→ "Requested format is not available"),
  set LOG_YTDLP_VERBOSE=1 in the environment to dump the full yt-dlp debug log.

  Ref: https://github.com/Brainicism/bgutil-ytdlp-pot-provider
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yt_dlp

log = logging.getLogger(__name__)

# Set LOG_YTDLP_VERBOSE=1 to print full yt-dlp debug output — useful when
# diagnosing "Requested format is not available" / DRM-only format issues.
_VERBOSE = os.getenv("LOG_YTDLP_VERBOSE", "0") == "1"

_COOKIES_PATH = "/app/cookies.txt"

_YDL_BASE: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": not _VERBOSE,
    "no_warnings": not _VERBOSE,
    "verbose": _VERBOSE,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "extractor_args": {
        "youtube": {
            # ios: 쿠키 직접 사용, signature 불필요 → 우선 시도
            # tv_embedded: 임베디드 클라이언트, SABR 미적용
            # web: bgutil PO 토큰 자동 주입 (폴백)
            # web_safari는 SABR 강제 적용되므로 제외
            "player_client": ["ios", "tv_embedded", "web"],
        }
    },
}

# FFmpeg reconnect flags – important for long streams
FFMPEG_OPTIONS: dict[str, str] = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

_executor = ThreadPoolExecutor(max_workers=4)


class _YtdlpLogger:
    """Routes yt-dlp messages to our Python logger (used when verbose=True)."""
    def debug(self, msg: str) -> None:
        log.debug("[yt-dlp] %s", msg)
    def warning(self, msg: str) -> None:
        log.warning("[yt-dlp] %s", msg)
    def error(self, msg: str) -> None:
        log.error("[yt-dlp] %s", msg)


# ── public API ────────────────────────────────────────────────────────────────

async def search_youtube(query: str) -> dict[str, Any]:
    """Return song info dict for *query* (title / URL / duration / thumbnail).

    Runs in a thread pool to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _extract_sync, query)


def _extract_sync(query: str) -> dict[str, Any]:
    options: dict[str, Any] = copy.deepcopy(_YDL_BASE)

    if os.path.exists(_COOKIES_PATH) and os.path.getsize(_COOKIES_PATH) > 0:
        options["cookiefile"] = _COOKIES_PATH
        log.info("yt-dlp: cookies loaded from %s (size=%d B)",
                 _COOKIES_PATH, os.path.getsize(_COOKIES_PATH))
    else:
        log.warning(
            "yt-dlp: cookies.txt not found or empty at %s — "
            "DRM-only formats likely; upload fresh cookies",
            _COOKIES_PATH,
        )

    if _VERBOSE:
        options["logger"] = _YtdlpLogger()

    # ── Step 1: flat search → get video URL without triggering format selection ──
    # Using extract_flat avoids "Requested format is not available" errors during
    # the ytsearch playlist processing phase.
    if not query.startswith("http"):
        flat_opts = copy.deepcopy(options)
        flat_opts["extract_flat"] = True
        flat_opts.pop("format", None)

        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            search_info = ydl.extract_info(f"ytsearch:{query}", download=False)

        if not search_info or not search_info.get("entries"):
            raise ValueError(f"검색 결과 없음: {query!r}")

        entry = search_info["entries"][0]
        # Prefer webpage_url (full canonical URL) over the plain id
        video_url = (
            entry.get("webpage_url")
            or entry.get("url")
            or f"https://www.youtube.com/watch?v={entry['id']}"
        )
        log.info("yt-dlp: search hit → %s (%s)", entry.get("title", "?"), video_url)
    else:
        video_url = query

    # ── Step 2: full extraction → format selection on the specific video URL ──
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(video_url, download=False)

        # In rare cases extract_info still wraps result in a playlist
        if "entries" in info:
            info = info["entries"][0]

        formats = info.get("formats", [])
        audio_fmts = [f for f in formats if f.get("acodec") not in (None, "none")]
        log.info(
            "yt-dlp: %s — %d formats total, %d with audio",
            info.get("title", "?"), len(formats), len(audio_fmts),
        )
        if not audio_fmts:
            # Log all format IDs/codecs so we can diagnose DRM vs storyboard
            log.warning(
                "yt-dlp: no audio formats found — available format_ids: %s",
                [f.get("format_id") for f in formats[:20]],
            )

        return {
            "title": info["title"],
            "url": info["url"],               # audio stream URL
            "webpage_url": info["webpage_url"],
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail"),
        }
