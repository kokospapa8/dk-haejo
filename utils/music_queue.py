"""
Per-guild music queue with repeat mode and volume control.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RepeatMode(Enum):
    OFF = "off"
    SINGLE = "single"
    QUEUE = "queue"


@dataclass
class Song:
    title: str
    webpage_url: str   # permanent YouTube page URL — used to fetch stream at play time
    duration: int      # seconds
    thumbnail: Optional[str]
    requested_by: str  # Discord display name
    video_id: str = ""
    # Stream URL is NOT stored here — it is fetched fresh in _play_audio()
    # so queued songs never hit 403 "stream URL expired" errors (~6 h TTL).


class MusicQueue:
    def __init__(self) -> None:
        self.queue: list[Song] = []
        self.current: Optional[Song] = None
        self.repeat_mode: RepeatMode = RepeatMode.OFF
        self.volume: float = 0.5  # 0.0 – 1.0
        self._lock = asyncio.Lock()

    # ── write ops ─────────────────────────────────────────────────────────────

    async def add(self, song: Song) -> None:
        async with self._lock:
            self.queue.append(song)

    async def next(self) -> Optional[Song]:
        """Advance to next song respecting repeat mode. Returns the song to play."""
        async with self._lock:
            if self.repeat_mode == RepeatMode.SINGLE and self.current:
                return self.current
            if self.repeat_mode == RepeatMode.QUEUE and self.current:
                self.queue.append(self.current)
            if self.queue:
                self.current = self.queue.pop(0)
                return self.current
            self.current = None
            return None

    async def remove(self, index: int) -> Optional[Song]:
        """Remove by 0-based index. Returns the removed song, or None."""
        async with self._lock:
            if 0 <= index < len(self.queue):
                return self.queue.pop(index)
            return None

    async def remove_multiple(self, indices: list[int]) -> list[Song]:
        """Remove songs at the given 0-based indices.

        Processes in descending order so earlier removals don't shift
        the positions of later ones. Returns the removed songs in the
        original (ascending) order, skipping out-of-range indices.
        """
        async with self._lock:
            valid = sorted(
                {i for i in indices if 0 <= i < len(self.queue)},
                reverse=True,  # remove from the end first
            )
            removed: list[Song] = []
            for idx in valid:
                removed.append(self.queue.pop(idx))
            removed.reverse()  # restore original order for the reply message
            return removed

    async def remove_by_title(self, title: str) -> Optional[Song]:
        """Remove the first queue entry whose title contains *title* (case-insensitive)."""
        async with self._lock:
            needle = title.lower()
            for i, song in enumerate(self.queue):
                if needle in song.title.lower():
                    return self.queue.pop(i)
            return None

    def clear(self) -> None:
        self.queue.clear()
        self.current = None

    # ── read ops ──────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.queue)
