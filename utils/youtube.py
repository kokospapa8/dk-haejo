"""
yt-dlp wrapper – async-safe YouTube audio extraction.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yt_dlp

# ── yt-dlp options ────────────────────────────────────────────────────────────

_YDL_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    # ios 클라이언트: 서버 환경에서 YouTube 봇 감지 우회
    "extractor_args": {
        "youtube": {
            "player_client": ["ios", "web"],
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
    with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
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
