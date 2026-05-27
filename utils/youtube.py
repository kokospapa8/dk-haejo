"""
yt-dlp wrapper – async-safe YouTube audio extraction.

Two-phase design:
  search_youtube(query)  → fast metadata (title, duration, thumbnail, webpage_url)
                           uses extract_flat=True, no format selection needed
  get_stream_url(url)    → actual audio stream URL, called right before playback
                           EJS scripts bundled via yt-dlp-ejs pip package

Why two phases?
  YouTube stream URLs expire in ~6 hours. Fetching the stream URL only at
  play time ensures queued songs never hit 403 errors.

Bot-detection bypass (EC2, 2025+):
  - yt-dlp-ejs pip package: bundles EJS signature/n-challenge solver scripts
    so yt-dlp never downloads them from GitHub at runtime
  - ios client + cookies: progressive m4a/opus, no SABR, no PO token needed
  - tv_embedded fallback: embedded client, no SABR
  - bgutil-ytdlp-pot-provider plugin (localhost:4416): auto-injects PO tokens

  web_safari is excluded: it forces SABR streaming (no direct URL).

Set LOG_YTDLP_VERBOSE=1 to dump full yt-dlp debug log to application logger.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yt_dlp

log = logging.getLogger(__name__)

_VERBOSE = os.getenv("LOG_YTDLP_VERBOSE", "0") == "1"
_COOKIES_PATH = "/app/cookies.txt"

# ── shared base options ───────────────────────────────────────────────────────

_COMMON: dict[str, Any] = {
    "quiet": not _VERBOSE,
    "no_warnings": not _VERBOSE,
    "verbose": _VERBOSE,
    "noplaylist": True,
    "source_address": "0.0.0.0",
}

# ── search options (flat, fast, no format selection) ─────────────────────────

_SEARCH_OPTS: dict[str, Any] = {
    **_COMMON,
    "extract_flat": True,
    "default_search": "ytsearch",
}

# ── playback options (full extraction, format selection, EJS via pip package) ─

_PLAY_OPTS: dict[str, Any] = {
    **_COMMON,
    # webm/opus: Discord-native codec, no transcoding → 1순위
    # m4a/aac:   high quality → 2순위
    # bestaudio: any audio-only → 3순위
    # best:      combined fallback → 4순위
    "format": "bestaudio[ext=webm][acodec=opus]/bestaudio[ext=m4a]/bestaudio/best",
    "extract_flat": False,
    # NOTE: remote_components NOT needed — yt-dlp-ejs pip package provides
    # the EJS scripts directly, so no GitHub download required at runtime.
    "extractor_args": {
        "youtube": {
            # ios: 쿠키 직접 사용, progressive m4a, SABR 없음 → 1순위
            # tv_embedded: 임베디드 클라이언트, SABR 미적용 → 2순위
            # web_safari 제외: SABR 강제 (direct URL 없음)
            "player_client": ["ios", "tv_embedded"],
        }
    },
}

# FFmpeg reconnect flags – critical for HTTP audio streams
FFMPEG_OPTIONS: dict[str, str] = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
        "-nostdin"
    ),
    "options": "-vn -loglevel warning",
}

_executor = ThreadPoolExecutor(max_workers=4)


class _YtdlpLogger:
    """Routes yt-dlp messages to our Python logger (used when verbose=True)."""
    def debug(self, msg: str) -> None:
        log.debug("[yt-dlp dbg] %s", msg)
    def warning(self, msg: str) -> None:
        log.warning("[yt-dlp] %s", msg)
    def error(self, msg: str) -> None:
        log.error("[yt-dlp] %s", msg)


def _apply_cookies(options: dict[str, Any]) -> None:
    """Attach cookiefile to options dict if the file exists and is non-empty."""
    if os.path.exists(_COOKIES_PATH) and os.path.getsize(_COOKIES_PATH) > 0:
        options["cookiefile"] = _COOKIES_PATH
        log.info("yt-dlp: cookies OK  path=%s  size=%d B",
                 _COOKIES_PATH, os.path.getsize(_COOKIES_PATH))
    else:
        log.warning(
            "yt-dlp: cookies MISSING or EMPTY at %s — "
            "DRM-only formats likely; please upload fresh cookies",
            _COOKIES_PATH,
        )


# ── public API ────────────────────────────────────────────────────────────────

async def search_youtube(query: str) -> dict[str, Any]:
    """Search YouTube and return song metadata (no stream URL).

    Fast — uses extract_flat so no format selection or signature solving needed.
    Returns: {title, video_id, webpage_url, duration, thumbnail}
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _search_sync, query)


async def get_stream_url(webpage_url: str) -> str:
    """Extract a fresh audio stream URL for *webpage_url*.

    Call this right before playback — YouTube stream URLs expire in ~6 hours.
    Raises yt_dlp.utils.DownloadError on failure.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _stream_sync, webpage_url)


# ── sync implementations (run in thread pool) ─────────────────────────────────

def _search_sync(query: str) -> dict[str, Any]:
    log.info("yt-dlp [search] START  query=%r", query)
    t0 = time.monotonic()

    opts = copy.deepcopy(_SEARCH_OPTS)
    _apply_cookies(opts)
    if _VERBOSE:
        opts["logger"] = _YtdlpLogger()

    search_query = query if query.startswith("http") else f"ytsearch:{query}"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(search_query, download=False)
    except Exception as exc:
        log.error("yt-dlp [search] FAILED  query=%r  error=%s", query, exc)
        raise

    # ytsearch returns a playlist wrapper; unwrap first entry
    if result and "entries" in result:
        result = result["entries"][0]

    if not result:
        log.error("yt-dlp [search] NO RESULTS  query=%r", query)
        raise ValueError(f"검색 결과 없음: {query!r}")

    video_id = result.get("id", "")
    webpage_url = (
        result.get("webpage_url")
        or result.get("url")
        or f"https://www.youtube.com/watch?v={video_id}"
    )
    elapsed = time.monotonic() - t0
    log.info(
        "yt-dlp [search] OK  title=%r  video_id=%s  duration=%ss  elapsed=%.2fs",
        result.get("title", "?"), video_id,
        result.get("duration") or "?", elapsed,
    )

    return {
        "title": result.get("title", "Unknown"),
        "video_id": video_id,
        "webpage_url": webpage_url,
        "duration": result.get("duration") or 0,
        "thumbnail": result.get("thumbnail"),
    }


def _stream_sync(webpage_url: str) -> str:
    log.info("yt-dlp [stream] START  url=%s", webpage_url)
    t0 = time.monotonic()

    opts = copy.deepcopy(_PLAY_OPTS)
    _apply_cookies(opts)
    if _VERBOSE:
        opts["logger"] = _YtdlpLogger()

    log.info(
        "yt-dlp [stream] options  format=%r  player_client=%s",
        opts.get("format"), opts["extractor_args"]["youtube"]["player_client"],
    )

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(webpage_url, download=False)
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log.error(
            "yt-dlp [stream] FAILED  url=%s  elapsed=%.2fs  error=%s",
            webpage_url, elapsed, exc,
        )
        raise

    if info and "entries" in info:
        info = info["entries"][0]

    stream_url: str = info["url"]
    elapsed = time.monotonic() - t0

    # Log selected format details
    fmt_id   = info.get("format_id", "?")
    fmt_ext  = info.get("ext", "?")
    acodec   = info.get("acodec", "?")
    abr      = info.get("abr") or info.get("tbr") or "?"
    n_fmts   = len(info.get("formats", []))
    n_audio  = len([f for f in info.get("formats", [])
                    if f.get("acodec") not in (None, "none")])

    log.info(
        "yt-dlp [stream] OK  title=%r  format_id=%s  ext=%s  acodec=%s  "
        "abr=%skbps  formats_total=%d  formats_with_audio=%d  elapsed=%.2fs",
        info.get("title", "?"), fmt_id, fmt_ext, acodec,
        abr, n_fmts, n_audio, elapsed,
    )

    if n_audio == 0:
        log.warning(
            "yt-dlp [stream] WARNING: 0 audio formats found — "
            "possible DRM or signature failure. "
            "Available format_ids: %s",
            [f.get("format_id") for f in info.get("formats", [])[:20]],
        )

    return stream_url
