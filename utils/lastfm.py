"""
Last.fm API client.

Provides:
  - parse_yt_title(yt_title) → (song, artist)
  - get_track_info(artist, title) → dict | None
  - get_similar_tracks(artist, title, limit) → list[{title, artist}]
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

_BASE = "https://ws.audioscrobbler.com/2.0/"
_UA   = "DiscordMusicBot/1.0"


# ── YouTube title parser ──────────────────────────────────────────────────────

def parse_yt_title(title: str) -> tuple[str, str]:
    """Extract (song_title, artist) from a YouTube video title.

    Handles common formats:
      "아이유 (IU) - Celebrity (Official MV)"  → ("Celebrity", "아이유")
      "BTS (방탄소년단) - Dynamite"              → ("Dynamite", "BTS")
      "Bohemian Rhapsody"                       → ("Bohemian Rhapsody", "")
    """
    def _strip_parens(s: str) -> str:
        return re.sub(r'\s*[\(\[][^\)\]]{1,50}[\)\]]', '', s).strip()

    def _strip_tags(s: str) -> str:
        return re.sub(
            r'\s*[\[\(]?\s*(?:official\s*(?:mv|video|audio|lyric[s]?)'
            r'|mv|lyric[s]?|4k|hd|live|performance|visualizer)\s*[\]\)]?',
            '', s, flags=re.IGNORECASE,
        ).strip(' -_|·•')

    if ' - ' in title:
        left, right = title.split(' - ', 1)
        artist = _strip_parens(left).strip()
        song   = _strip_tags(_strip_parens(right)).strip()
    else:
        artist = ''
        song   = _strip_tags(_strip_parens(title)).strip()

    return (song or title), artist


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(params: dict[str, Any]) -> dict | None:
    api_key = os.getenv("LASTFM_API_KEY", "")
    if not api_key:
        log.warning("lastfm: LASTFM_API_KEY not set — skipping API call")
        return None
    params = {**params, "api_key": api_key, "format": "json"}
    url = f"{_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        if "error" in data:
            log.warning("lastfm: API error %s — %s", data["error"], data.get("message"))
            return None
        return data
    except Exception as exc:
        log.warning("lastfm: request failed: %s", exc)
        return None


# ── public API ────────────────────────────────────────────────────────────────

def get_track_info(artist: str, title: str) -> dict | None:
    """Return Last.fm track metadata dict, or None if not found."""
    if not artist or not title:
        return None
    data = _get({"method": "track.getInfo", "artist": artist, "track": title})
    return data.get("track") if data else None


def get_top_tags(artist: str, title: str) -> list[str]:
    """Return up to 5 genre/mood tags for the track."""
    info = get_track_info(artist, title)
    if not info:
        return []
    tags = info.get("toptags", {}).get("tag", [])
    if isinstance(tags, dict):
        tags = [tags]
    return [t["name"] for t in tags[:5] if t.get("name")]


def get_similar_tracks(artist: str, title: str, limit: int = 15) -> list[dict]:
    """Return similar tracks as list of {title, artist}."""
    if not artist or not title:
        return []
    data = _get({
        "method": "track.getSimilar",
        "artist": artist,
        "track": title,
        "limit": limit,
        "autocorrect": 1,
    })
    if not data:
        return []
    similar = data.get("similartracks", {}).get("track", [])
    if isinstance(similar, dict):
        similar = [similar]
    return [
        {"title": t["name"], "artist": t["artist"]["name"]}
        for t in similar
        if t.get("name") and isinstance(t.get("artist"), dict)
    ]
