"""
Per-user track storage with Last.fm metadata.

Storage: /app/data/user_tracks.json
Format:
  {
    "<user_id>": {
      "display_name": "jinwook",
      "tracks": [          # sorted newest-first; deduplicated by webpage_url
        {
          "title":         "Celebrity",
          "artist":        "IU",
          "webpage_url":   "https://youtube.com/watch?v=...",
          "video_id":      "...",
          "lastfm_title":  "Celebrity",
          "lastfm_artist": "IU",
          "tags":          ["k-pop", "korean pop"],
          "play_count":    3
        },
        ...
      ]
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PATH = Path(os.getenv("USER_TRACKS_PATH", "/app/data/user_tracks.json"))
MAX_TRACKS_PER_USER = 300


def _load() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        with _PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("user_tracks: load failed: %s", exc)
        return {}


def _save(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = _PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_PATH)
    except Exception as exc:
        log.error("user_tracks: save failed: %s", exc)


def add_track(
    user_id: int,
    display_name: str,
    *,
    title: str,
    artist: str,
    webpage_url: str,
    video_id: str = "",
    lastfm_title: str = "",
    lastfm_artist: str = "",
    tags: list[str] | None = None,
) -> None:
    """Record a played track for a user. Increments play_count on duplicates."""
    data = _load()
    key = str(user_id)
    user: dict[str, Any] = data.setdefault(key, {"display_name": display_name, "tracks": []})
    user["display_name"] = display_name

    tracks: list[dict] = user["tracks"]
    for t in tracks:
        if t.get("webpage_url") == webpage_url:
            t["play_count"] = t.get("play_count", 1) + 1
            if lastfm_title:
                t["lastfm_title"]  = lastfm_title
                t["lastfm_artist"] = lastfm_artist
            if tags:
                t["tags"] = tags
            _save(data)
            return

    tracks.insert(0, {
        "title":         title,
        "artist":        artist,
        "webpage_url":   webpage_url,
        "video_id":      video_id,
        "lastfm_title":  lastfm_title or title,
        "lastfm_artist": lastfm_artist or artist,
        "tags":          tags or [],
        "play_count":    1,
    })
    user["tracks"] = tracks[:MAX_TRACKS_PER_USER]
    _save(data)


def get_user_tracks(user_id: int, limit: int = 50) -> list[dict]:
    """Return user's tracks sorted by play_count (desc), newest-first within same count."""
    tracks = _load().get(str(user_id), {}).get("tracks", [])
    return sorted(tracks, key=lambda t: t.get("play_count", 1), reverse=True)[:limit]


def find_user_id_by_name(display_name: str) -> int | None:
    """Return user_id for the first user whose display_name matches (case-insensitive)."""
    needle = display_name.lower()
    for uid, udata in _load().items():
        if udata.get("display_name", "").lower() == needle:
            return int(uid)
    return None
