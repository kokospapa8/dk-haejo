"""
Per-guild music queue with repeat mode and volume control.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    requester_id: int = 0  # Discord user ID (0 = unknown)
    # Stream URL is NOT stored here — it is fetched fresh in _play_audio()
    # so queued songs never hit 403 "stream URL expired" errors (~6 h TTL).


class MusicQueue:
    def __init__(self) -> None:
        self.queue: list[Song] = []
        self.current: Optional[Song] = None
        self.repeat_mode: RepeatMode = RepeatMode.OFF
        self.volume: float = 0.5  # 0.0 – 1.0
        self._lock = asyncio.Lock()
        # Playback timer — used by the Now Playing panel for the progress bar.
        self._play_started_at: Optional[datetime] = None
        self._pause_elapsed: float = 0.0   # seconds elapsed before current pause

    # ── playback timer ────────────────────────────────────────────────────────

    def on_play(self) -> None:
        """Call when a new song starts (not resume)."""
        self._play_started_at = datetime.now(timezone.utc)
        self._pause_elapsed = 0.0

    def on_pause(self) -> None:
        """Call when playback is paused."""
        if self._play_started_at is not None:
            self._pause_elapsed += (datetime.now(timezone.utc) - self._play_started_at).total_seconds()
            self._play_started_at = None

    def on_resume(self) -> None:
        """Call when playback is resumed after a pause."""
        self._play_started_at = datetime.now(timezone.utc)

    def get_elapsed(self) -> float:
        """Return estimated playback position in seconds."""
        elapsed = self._pause_elapsed
        if self._play_started_at is not None:
            elapsed += (datetime.now(timezone.utc) - self._play_started_at).total_seconds()
        return max(0.0, elapsed)

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

    async def move(self, from_index: int, to_index: int) -> Optional[Song]:
        """Move a song from from_index to to_index (0-based). Returns the song, or None."""
        async with self._lock:
            if not (0 <= from_index < len(self.queue)):
                return None
            song = self.queue.pop(from_index)
            insert_at = max(0, min(to_index, len(self.queue)))
            self.queue.insert(insert_at, song)
            return song

    def clear(self) -> None:
        self.queue.clear()
        self.current = None

    # ── read ops ──────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.queue)
