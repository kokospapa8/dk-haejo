"""
yt-dlp wrapper – async-safe YouTube audio extraction.

Bot-detection bypass strategy (layered):
  1. web_creator player client — YouTube Studio client; cloud IPs get fewer restrictions
  2. cookies.txt (Netscape format) — checked at request time, not import time
  3. tv_embedded / ios as fallback clients
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

# ── yt-dlp options ────────────────────────────────────────────────────────────

_COOKIES_PATH = "/app/cookies.txt"

# Base options.
# player_client priority:
#   web_creator → YouTube Studio API; typically not subject to the same bot checks
#   tv_embedded → Connected-TV embedded player; relaxed policy
#   ios         → mobile fallback
_YDL_BASE: dict[str, Any] = {
    # bestaudio* = 오디오 전용 스트림 우선, 없으면 비디오+오디오 혼합도 허용
    "format": "bestaudio*",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    # Node.js로 YouTube 서명(n-function) 복호화
    # 없으면 스트림 URL을 얻지 못하고 "Only images available" 에러 발생
    "js_runtimes": "node",
    "extractor_args": {
        "youtube": {
            # web = 가장 많은 포맷 제공, 쿠키 인증으로 bot check 통과
            "player_client": ["web", "tv_embedded", "ios"],
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
    # Deep-copy so yt-dlp can't mutate shared state between concurrent calls
    options: dict[str, Any] = copy.deepcopy(_YDL_BASE)

    # 쿠키 파일 체크를 요청 시점에 수행 (모듈 임포트 시점이 아님)
    # → 컨테이너 기동 후 cookies.txt가 업로드되어도 즉시 반영됨
    if os.path.exists(_COOKIES_PATH) and os.path.getsize(_COOKIES_PATH) > 0:
        options["cookiefile"] = _COOKIES_PATH
        log.info("yt-dlp: cookies loaded from %s", _COOKIES_PATH)
    else:
        log.warning(
            "yt-dlp: cookies.txt not found or empty at %s — "
            "relying on web_creator/tv_embedded player clients only",
            _COOKIES_PATH,
        )

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
