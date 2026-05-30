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
    # extractor_args(player_client) 지정 안 함 → yt-dlp 기본 클라이언트 조합 사용
    # yt-dlp가 내부적으로 web_safari(메타데이터), tv(포맷 추출) 등을 조합함
    # ios는 쿠키와 충돌하여 스킵됨, tv_embedded는 존재하지 않는 이름(올바른 이름: tv)
    # bgutil PO Token 사이드카가 SABR 처리하므로 기본값으로 충분
    "remote_components": ["ejs:github"],
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


async def search_youtube_multi(query: str, count: int = 10) -> list[dict[str, Any]]:
    """Search YouTube and return up to *count* results (metadata only, no stream URL).

    Uses ytsearch{N}: prefix so a single yt-dlp call returns multiple entries.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _search_multi_sync, query, min(count, 10))


async def fetch_from_url(url: str, max_entries: int = 50) -> list[dict[str, Any]]:
    """Fetch metadata from a YouTube video or playlist URL.

    Returns a list of {title, video_id, webpage_url, duration, thumbnail}.
    Single video → 1-item list.  Playlist → up to max_entries items.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _fetch_url_sync, url, max_entries)


async def get_stream_url(webpage_url: str, max_retries: int = 2) -> str:
    """Extract a fresh audio stream URL for *webpage_url*.

    Call this right before playback — YouTube stream URLs expire in ~6 hours.
    Retries up to *max_retries* times with exponential backoff (1 s, 1.5 s).
    Raises yt_dlp.utils.DownloadError after all attempts are exhausted.
    """
    loop = asyncio.get_event_loop()
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await loop.run_in_executor(_executor, _stream_sync, webpage_url)
        except yt_dlp.utils.DownloadError as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 1.5 ** attempt  # 1.0 s, 1.5 s
                log.warning(
                    "yt-dlp [stream] retry %d/%d after %.1fs  reason=%s",
                    attempt + 1, max_retries, wait, exc,
                )
                await asyncio.sleep(wait)
            else:
                log.error(
                    "yt-dlp [stream] all %d attempts failed  url=%s",
                    max_retries + 1, webpage_url,
                )

    raise last_exc  # type: ignore[misc]


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


def _search_multi_sync(query: str, count: int) -> list[dict[str, Any]]:
    log.info("yt-dlp [search_multi] START  query=%r  count=%d", query, count)
    t0 = time.monotonic()

    opts = copy.deepcopy(_SEARCH_OPTS)
    _apply_cookies(opts)
    if _VERBOSE:
        opts["logger"] = _YtdlpLogger()

    search_query = query if query.startswith("http") else f"ytsearch{count}:{query}"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(search_query, download=False)
    except Exception as exc:
        log.error("yt-dlp [search_multi] FAILED  query=%r  error=%s", query, exc)
        raise

    entries = [e for e in (result.get("entries") or []) if e][:count]
    elapsed = time.monotonic() - t0
    log.info(
        "yt-dlp [search_multi] OK  query=%r  results=%d  elapsed=%.2fs",
        query, len(entries), elapsed,
    )

    return [
        {
            "title": e.get("title", "Unknown"),
            "video_id": e.get("id", ""),
            "webpage_url": (
                e.get("webpage_url")
                or e.get("url")
                or f"https://www.youtube.com/watch?v={e.get('id', '')}"
            ),
            "duration": e.get("duration") or 0,
            "thumbnail": e.get("thumbnail"),
        }
        for e in entries
    ]


def _fetch_url_sync(url: str, max_entries: int) -> list[dict[str, Any]]:
    log.info("yt-dlp [fetch_url] START  url=%s  max=%d", url, max_entries)
    t0 = time.monotonic()

    opts = {
        **copy.deepcopy(_SEARCH_OPTS),
        "noplaylist": False,   # allow playlist expansion
        "playlistend": max_entries,
    }
    _apply_cookies(opts)
    if _VERBOSE:
        opts["logger"] = _YtdlpLogger()

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
    except Exception as exc:
        log.error("yt-dlp [fetch_url] FAILED  url=%s  error=%s", url, exc)
        raise

    # Playlist → entries list; single video → wrap in list
    if result and "entries" in result:
        entries = [e for e in (result["entries"] or []) if e][:max_entries]
    else:
        entries = [result] if result else []

    elapsed = time.monotonic() - t0
    log.info("yt-dlp [fetch_url] OK  url=%s  count=%d  elapsed=%.2fs", url, len(entries), elapsed)

    return [
        {
            "title": e.get("title", "Unknown"),
            "video_id": e.get("id", ""),
            "webpage_url": (
                e.get("webpage_url")
                or e.get("url")
                or f"https://www.youtube.com/watch?v={e.get('id', '')}"
            ),
            "duration": e.get("duration") or 0,
            "thumbnail": e.get("thumbnail"),
        }
        for e in entries
    ]


def _stream_sync(webpage_url: str) -> str:
    log.info("yt-dlp [stream] START  url=%s", webpage_url)
    t0 = time.monotonic()

    opts = copy.deepcopy(_PLAY_OPTS)
    _apply_cookies(opts)
    if _VERBOSE:
        opts["logger"] = _YtdlpLogger()

    log.info(
        "yt-dlp [stream] options  format=%r  remote_components=%s",
        opts.get("format"), opts.get("remote_components"),
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
        # On format-not-available errors, run a diagnostic extraction with no
        # format selector so we can see exactly what formats YouTube returned.
        if "format is not available" in str(exc).lower():
            _diagnose_formats(webpage_url, opts)
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


def _diagnose_formats(webpage_url: str, base_opts: dict) -> None:
    """Re-extract with no format selector and full verbose output.

    Called automatically when 'Requested format is not available' is raised.
    Logs format_id / ext / acodec / vcodec / url_present for every format so
    we can tell: DRM-only? storyboard-only? signature-solving failure?
    """
    log.warning("yt-dlp [diag] Running diagnostic extraction (no format filter)...")
    try:
        diag_opts = copy.deepcopy(base_opts)
        diag_opts.pop("format", None)          # no format constraint
        diag_opts["quiet"] = False
        diag_opts["verbose"] = True
        diag_opts["logger"] = _YtdlpLogger()  # route yt-dlp debug to our logger

        with yt_dlp.YoutubeDL(diag_opts) as ydl:
            info = ydl.extract_info(webpage_url, download=False, process=False)

        if not info:
            log.warning("yt-dlp [diag] extract returned None")
            return

        fmts = info.get("formats") or []
        log.warning(
            "yt-dlp [diag] %d raw formats (process=False):", len(fmts)
        )
        for f in fmts[:30]:
            log.warning(
                "yt-dlp [diag]   id=%-6s  ext=%-5s  acodec=%-12s  vcodec=%-12s"
                "  has_url=%s  has_drm=%s",
                f.get("format_id", "?"),
                f.get("ext", "?"),
                f.get("acodec", "?"),
                f.get("vcodec", "?"),
                bool(f.get("url")),
                f.get("has_drm", False),
            )
        if not fmts:
            log.warning("yt-dlp [diag] No formats in raw info. Info keys: %s",
                        list(info.keys()))
    except Exception as diag_exc:
        log.warning("yt-dlp [diag] Diagnostic extraction also failed: %s", diag_exc)
