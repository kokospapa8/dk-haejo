"""
Per-user personal playlists, persisted to a JSON file.

Storage: /app/data/playlists.json  (bind-mounted from ./data on the host)
Format:
  {
    "<guild_id>": {
      "<user_id>": {
        "username": "jinwook",        # display name — updated on each write
        "songs": [
          {
            "title":       "BTS - Dynamite",
            "webpage_url": "https://...",
            "duration":    199,
            "video_id":    "...",
            "thumbnail":   "...",
            "added_at":    "2026-05-28T12:34:56+00:00"
          },
          ...                         # insertion order preserved
        ]
      }
    }
  }

Rules:
- Max MAX_PER_USER (100) songs per playlist.
- Adding a duplicate video_id moves it to the END (keep insertion order, dedupe).
- Username is updated on every write so it always reflects the latest display name.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PLAYLIST_PATH = Path(os.getenv("PLAYLIST_PATH", "/app/data/playlists.json"))
MAX_PER_USER = 100


# ── private helpers ───────────────────────────────────────────────────────────

def _load() -> dict[str, dict[str, dict]]:
    if not _PLAYLIST_PATH.exists():
        return {}
    try:
        with _PLAYLIST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("playlist: load failed (%s): %s", _PLAYLIST_PATH, exc)
        return {}


def _save(data: dict) -> None:
    _PLAYLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = _PLAYLIST_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_PLAYLIST_PATH)
    except Exception as exc:
        log.error("playlist: save failed (%s): %s", _PLAYLIST_PATH, exc)


def _user_entry(data: dict, guild_id: int, user_id: str) -> dict:
    """Return (and ensure) the user entry dict inside *data*."""
    gk = str(guild_id)
    if gk not in data:
        data[gk] = {}
    if user_id not in data[gk]:
        data[gk][user_id] = {"username": "", "songs": []}
    return data[gk][user_id]


# ── public API ────────────────────────────────────────────────────────────────

def add_song(
    guild_id: int,
    user_id: str,
    username: str,
    song: Any,                  # Song dataclass or dict
) -> tuple[bool, int]:
    """Add *song* to the user's playlist.

    Returns (already_existed, new_total).
    Duplicate video_id is removed first then re-appended (moves to end).
    Caps at MAX_PER_USER; returns False if the cap is already reached and
    the song is truly new.
    """
    data = _load()
    entry = _user_entry(data, guild_id, user_id)
    entry["username"] = username  # keep display name fresh

    video_id: str = (
        getattr(song, "video_id", None)
        or (song.get("video_id") if isinstance(song, dict) else "")
        or ""
    )

    new_song: dict[str, Any] = {
        "title":       getattr(song, "title",       None) or (song.get("title")       if isinstance(song, dict) else "Unknown") or "Unknown",
        "webpage_url": getattr(song, "webpage_url", None) or (song.get("webpage_url") if isinstance(song, dict) else "")       or "",
        "duration":    getattr(song, "duration",    None) or (song.get("duration")    if isinstance(song, dict) else 0)        or 0,
        "video_id":    video_id,
        "thumbnail":   getattr(song, "thumbnail",   None) or (song.get("thumbnail")   if isinstance(song, dict) else None),
        "added_at":    datetime.now(timezone.utc).isoformat(),
    }

    songs: list[dict] = entry["songs"]
    already_existed = video_id and any(s.get("video_id") == video_id for s in songs)

    # Remove duplicate so it moves to the end
    if already_existed and video_id:
        songs[:] = [s for s in songs if s.get("video_id") != video_id]

    if len(songs) >= MAX_PER_USER and not already_existed:
        return False, len(songs)   # cap reached, song not added

    songs.append(new_song)
    entry["songs"] = songs[:MAX_PER_USER]
    _save(data)
    log.debug("playlist: add  guild=%s  user=%s  title=%r", guild_id, user_id, new_song["title"])
    return already_existed, len(entry["songs"])


def remove_songs(
    guild_id: int,
    user_id: str,
    zero_based_indices: list[int],
) -> list[dict]:
    """Remove songs at the given 0-based indices. Returns removed songs."""
    data = _load()
    entry = _user_entry(data, guild_id, user_id)
    songs = entry["songs"]

    valid = sorted({i for i in zero_based_indices if 0 <= i < len(songs)}, reverse=True)
    removed: list[dict] = []
    for idx in valid:
        removed.append(songs.pop(idx))
    removed.reverse()

    _save(data)
    return removed


def get_playlist(guild_id: int, user_id: str) -> tuple[str, list[dict]]:
    """Return (username, songs) for this user. Username is '' if no playlist yet."""
    data = _load()
    entry = data.get(str(guild_id), {}).get(user_id)
    if not entry:
        return "", []
    return entry.get("username", ""), entry.get("songs", [])


def find_by_username(
    guild_id: int, target_name: str
) -> tuple[str | None, str | None, list[dict]]:
    """Find a playlist by display name (case-insensitive partial match).

    Returns (user_id, username, songs).  All three are None / [] if not found.
    Prefers exact match over partial; returns the first partial match.
    """
    data = _load()
    guild_data = data.get(str(guild_id), {})
    needle = target_name.lower()

    exact: tuple[str, str, list] | None = None
    partial: tuple[str, str, list] | None = None

    for uid, entry in guild_data.items():
        stored_name: str = entry.get("username", "")
        if stored_name.lower() == needle:
            exact = (uid, stored_name, entry.get("songs", []))
            break
        if needle in stored_name.lower() and partial is None:
            partial = (uid, stored_name, entry.get("songs", []))

    result = exact or partial
    if result:
        return result
    return None, None, []
