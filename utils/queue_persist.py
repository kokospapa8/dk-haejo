"""
Queue persistence — saves and restores the per-guild playback queue.

Storage: /app/data/queue_state.json  (bind-mounted from ./data on the host)
Format:
  {
    "<guild_id>": {
      "songs": [                   # current song (index 0) + upcoming queue
        {"title": ..., "webpage_url": ..., "duration": ...,
         "thumbnail": ..., "requested_by": ..., "video_id": ...},
        ...
      ],
      "repeat_mode": "off",        # "off" | "single" | "queue"
      "volume": 0.5,
      "text_channel_id": 123456    # last active text channel
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

_PATH = Path(os.getenv("QUEUE_STATE_PATH", "/app/data/queue_state.json"))


def _load_all() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        with _PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("queue_persist: load failed: %s", exc)
        return {}


def _save_all(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = _PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_PATH)
    except Exception as exc:
        log.error("queue_persist: save failed: %s", exc)


def _song_to_dict(song: Any) -> dict:
    def _g(k: str, default: Any = "") -> Any:
        return song.get(k, default) if isinstance(song, dict) else getattr(song, k, default)
    return {
        "title":        _g("title",        "Unknown"),
        "webpage_url":  _g("webpage_url",  ""),
        "duration":     _g("duration",     0),
        "thumbnail":    _g("thumbnail"),
        "requested_by": _g("requested_by", "?"),
        "video_id":     _g("video_id",     ""),
    }


def save(guild_id: int, queue: Any, text_channel_id: int | None = None) -> None:
    """Persist current + upcoming songs for a guild."""
    songs: list[dict] = []
    if queue.current:
        songs.append(_song_to_dict(queue.current))
    for s in queue.queue:
        songs.append(_song_to_dict(s))

    data = _load_all()
    entry: dict[str, Any] = {
        "songs":       songs,
        "repeat_mode": queue.repeat_mode.value,
        "volume":      queue.volume,
    }
    if text_channel_id:
        entry["text_channel_id"] = text_channel_id

    if songs:
        data[str(guild_id)] = entry
    else:
        data.pop(str(guild_id), None)   # nothing to restore — remove stale entry

    _save_all(data)


def load(guild_id: int) -> dict | None:
    """Return saved state for a guild, or None if nothing saved."""
    return _load_all().get(str(guild_id))


def clear(guild_id: int) -> None:
    """Remove saved state for a guild (called on explicit stop/clear)."""
    data = _load_all()
    if str(guild_id) in data:
        data.pop(str(guild_id))
        _save_all(data)
