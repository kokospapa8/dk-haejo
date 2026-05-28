"""
Per-guild playback history, persisted to a JSON file.

Storage: /app/data/history.json  (bind-mounted from ./data on the host)
Format:
  {
    "<guild_id>": [          # newest first
      {
        "title":       "BTS - Dynamite",
        "webpage_url": "https://www.youtube.com/watch?v=...",
        "duration":    199,
        "video_id":    "gdZLi9oWNZg",
        "thumbnail":   "https://...",
        "requested_by": "jinwook",
        "played_at":   "2026-05-28T12:34:56+00:00"
      },
      ...
    ]
  }

Rules:
- Max MAX_PER_GUILD (100) entries per guild — oldest dropped automatically.
- Duplicate video_id is moved to front rather than duplicated.
- File I/O is synchronous but fast (<1 ms for 100 entries).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "/app/data/history.json"))
MAX_PER_GUILD = 100


# ── private helpers ───────────────────────────────────────────────────────────

def _load() -> dict[str, list[dict]]:
    if not _HISTORY_PATH.exists():
        return {}
    try:
        with _HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("history: load failed (%s): %s", _HISTORY_PATH, exc)
        return {}


def _save(data: dict[str, list[dict]]) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = _HISTORY_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_HISTORY_PATH)  # atomic replace
    except Exception as exc:
        log.error("history: save failed (%s): %s", _HISTORY_PATH, exc)


# ── public API ────────────────────────────────────────────────────────────────

def add_song(guild_id: int, song: Any) -> None:
    """Prepend *song* to the guild's history (deduplicates by video_id).

    *song* may be a Song dataclass or a plain dict with the same keys.
    """
    data = _load()
    key = str(guild_id)
    entries: list[dict] = data.get(key, [])

    video_id = getattr(song, "video_id", None) or (
        song.get("video_id") if isinstance(song, dict) else ""
    ) or ""

    def _g(key: str, default: Any = None) -> Any:
        return song.get(key, default) if isinstance(song, dict) else getattr(song, key, default)

    entry: dict[str, Any] = {
        "title":        _g("title",        "Unknown"),
        "webpage_url":  _g("webpage_url",  ""),
        "duration":     _g("duration",     0),
        "video_id":     video_id,
        "thumbnail":    _g("thumbnail"),
        "requested_by": _g("requested_by", "?"),
        "played_at":    datetime.now(timezone.utc).isoformat(),
    }

    # Remove duplicate so the same song moves to front rather than duplicating
    if video_id:
        entries = [e for e in entries if e.get("video_id") != video_id]

    entries.insert(0, entry)
    data[key] = entries[:MAX_PER_GUILD]
    _save(data)
    log.debug("history: saved  guild=%s  title=%r  by=%r", guild_id, entry["title"], entry["requested_by"])


def get_history(guild_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Return up to *limit* recent songs for this guild (newest first)."""
    data = _load()
    return data.get(str(guild_id), [])[:limit]
