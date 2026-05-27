"""
yt-dlp wrapper – async-safe YouTube audio extraction.

Bot-detection bypass strategy (layered):
  1. tv_embedded player client → no login required for most videos
  2. cookies.txt (Netscape format) → checked at request time, not import time
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yt_dlp

log = logging.getLogger(__name__)

# ── yt-dlp options ────────────────────────────────────────────────────────────

_COOKIES_PATH = "/app/cookies.txt"

# Base options — player_client=tv_embedded bypasses bot-check for most videos
# without requiring authentication (TV clients get a relaxed policy from YouTube)
_YDL_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    # tv_embedded player client → doesn't trigger sign-in prompt on cloud IPs
    "extractor_args": {
        "youtube": {
            "player_client": ["tv_embedded", "ios"],
        }
    },
}

# FFmpeg reconnect flags – important for long streams
FFMPEG_OPTIONS: dict[str, str] = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

_executor = ThreadPoolExecutor(max_workers=4)


# ── public API ────────────────────────────────────────────────────────────────

async def search_youtube(query: str) -> dict[str, Any]:
    """Return song info dict for *query* (title / URL / duration / thumbnail).

    If *query* is not a URL, prefixes it with ``ytsearch:`` for a YouTube search.
    Runs in a thread pool to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _extract_sync, query)


def _extract_sync(query: str) -> dict[str, Any]:
    # 쿠키 파일 체크를 요청 시점에 수행 (모듈 임포트 시점이 아님)
    # → 컨테이너 기동 후 cookies.txt가 업로드되어도 즉시 반영됨
    options = dict(_YDL_OPTIONS)
    if os.path.exists(_COOKIES_PATH) and os.path.getsize(_COOKIES_PATH) > 0:
        options["cookiefile"] = _COOKIES_PATH
        log.debug("yt-dlp: using cookies from %s", _COOKIES_PATH)
    else:
        log.debug("yt-dlp: no cookies file, relying on tv_embedded player client")

    with yt_dlp.YoutubeDL(options) as ydl:
        if not query.startswith("http"):
            query = f"ytsearch:{query}"
        info = ydl.extract_info(query, download=False)

        # If it's a search result, take the first entry
        if "entries" in info:
            info = info["entries"][0]

        return {
            "title": info["title"],
            "url": info["url"],               # audio stream URL
            "webpage_url": info["webpage_url"],
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail"),
        }
