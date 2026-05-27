"""
yt-dlp wrapper – async-safe YouTube audio extraction.

Bot-detection bypass strategy (EC2, 2025+):
  1. ios client + cookies  — iOS client uses cookies natively; no PO token needed.
     Works when the cookie session is valid.  Try this first.
  2. web client + PO token — bgutil-ytdlp-pot-provider sidecar (localhost:4416)
     auto-injects Proof-of-Origin tokens so YouTube accepts the web client.
     Fallback when ios is blocked.

  Without proper auth YouTube returns only DRM/SABR streams, which yt-dlp
  excludes from format selection → "Requested format is not available".
  Valid cookies (or a PO token) give back real audio streams.

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

# ── yt-dlp options ────────────────────────────────────────────────────────────

_COOKIES_PATH = "/app/cookies.txt"

_YDL_BASE: dict[str, Any] = {
    # bestaudio/best: 오디오 전용 스트림 우선, 없으면 최고 품질 스트림
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "extractor_args": {
        "youtube": {
            # ios 클라이언트: 쿠키를 직접 사용, PO 토큰 불필요, non-DRM 포맷 반환
            # web 클라이언트: bgutil 사이드카가 PO 토큰 자동 주입 (폴백)
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
            "DRM-only formats likely; try uploading fresh cookies",
            _COOKIES_PATH,
        )

    with yt_dlp.YoutubeDL(options) as ydl:
        if not query.startswith("http"):
            query = f"ytsearch:{query}"
        info = ydl.extract_info(query, download=False)

        # If it's a search result, take the first entry
        if "entries" in info:
            info = info["entries"][0]

        # Diagnostic: log available format count so we can tell DRM-only vs real
        formats = info.get("formats", [])
        audio_fmts = [f for f in formats if f.get("acodec") not in (None, "none")]
        log.debug(
            "yt-dlp: %s — %d total formats, %d with audio (video_id=%s)",
            info.get("title", "?"),
            len(formats),
            len(audio_fmts),
            info.get("id", "?"),
        )

        return {
            "title": info["title"],
            "url": info["url"],               # audio stream URL
            "webpage_url": info["webpage_url"],
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail"),
        }
