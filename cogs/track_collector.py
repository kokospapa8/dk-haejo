"""
Track Collector — records each played song to the requesting user's track history
and enriches it with Last.fm metadata (artist, tags) in the background.

Triggered by on_music_state_change; only fires when the song URL actually changes.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from utils import history as hist
from utils import lastfm, user_tracks
from utils.music_queue import Song
from utils.youtube import search_youtube

log = logging.getLogger(__name__)


class TrackCollector(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # guild_id → last recorded webpage_url
        self._last_url: dict[int, str] = {}

    @commands.Cog.listener()
    async def on_music_state_change(self, guild: discord.Guild) -> None:
        music = self.bot.cogs.get("Music")
        if not music:
            return

        song = music.get_current_song(guild)  # type: ignore[union-attr]
        if not song or not song.requester_id:
            return

        if self._last_url.get(guild.id) == song.webpage_url:
            return
        self._last_url[guild.id] = song.webpage_url

        # Fire-and-forget: don't block playback
        asyncio.create_task(self._record(song))

    async def _record(self, song) -> None:  # type: ignore[type-arg]
        loop = asyncio.get_running_loop()

        # Parse YouTube title → artist/song name for Last.fm lookup
        yt_song, yt_artist = lastfm.parse_yt_title(song.title)

        # Fetch Last.fm metadata in thread pool (blocking I/O)
        lf_title, lf_artist, tags = await loop.run_in_executor(
            None, self._fetch_lastfm, yt_artist, yt_song
        )

        await loop.run_in_executor(
            None,
            lambda: user_tracks.add_track(
                song.requester_id,
                song.requested_by,
                title=yt_song,
                artist=yt_artist,
                webpage_url=song.webpage_url,
                video_id=song.video_id,
                lastfm_title=lf_title,
                lastfm_artist=lf_artist,
                tags=tags,
            ),
        )
        log.debug(
            "track_collector: recorded %r by user %s (lastfm: %r / %r, tags: %s)",
            song.title, song.requester_id, lf_title, lf_artist, tags,
        )

    # ── auto-recommend on queue exhausted ────────────────────────────────────

    @commands.Cog.listener()
    async def on_queue_exhausted(self, guild: discord.Guild) -> None:
        """Fill queue with 3 recommendations when auto_recommend mode is ON."""
        music = self.bot.cogs.get("Music")
        if not music:
            return
        queue = music._get_queue(guild.id)  # type: ignore[union-attr]

        added = await self._fill_recommendations(guild, queue)

        if added > 0:
            await music._play_next(guild)  # type: ignore[union-attr]
        else:
            music._start_idle_timer(guild)  # type: ignore[union-attr]
            self.bot.dispatch("music_state_change", guild)

    async def _fill_recommendations(self, guild: discord.Guild, queue) -> int:  # type: ignore[type-arg]
        """Fetch 1 Last.fm-based recommendation and add it to the queue. Returns count added."""
        loop = asyncio.get_running_loop()
        history = await loop.run_in_executor(None, hist.get_history, guild.id, 10)
        if not history:
            return 0

        # Collect similar tracks from up to 5 recent seeds
        similar: list[dict] = []
        for entry in history[:5]:
            yt_song, yt_artist = lastfm.parse_yt_title(entry["title"])
            if not yt_artist:
                continue
            result = await loop.run_in_executor(
                None, lastfm.get_similar_tracks, yt_artist, yt_song, 15
            )
            similar.extend(result)
            if len(similar) >= 20:
                break

        if not similar:
            return 0

        # Deduplicate and exclude recently played
        played_urls = {e["webpage_url"] for e in history}
        seen: set[str] = set()
        candidates: list[dict] = []
        for t in similar:
            key = f"{t['artist']}|{t['title']}".lower()
            if key not in seen:
                seen.add(key)
                candidates.append(t)

        added = 0
        for rec in candidates:
            if added >= 1:
                break
            try:
                yt = await search_youtube(f"{rec['artist']} {rec['title']}")
                if not yt or yt.get("webpage_url") in played_urls:
                    continue
                song = Song(
                    title=yt["title"],
                    webpage_url=yt["webpage_url"],
                    duration=yt.get("duration", 0),
                    thumbnail=yt.get("thumbnail"),
                    requested_by="🤖 자동추천",
                    video_id=yt.get("video_id", ""),
                    requester_id=0,
                )
                await queue.add(song)
                played_urls.add(yt["webpage_url"])
                added += 1
                log.info("auto_recommend: added %r to guild %s", yt["title"], guild.id)
            except Exception as exc:
                log.debug("auto_recommend: skipped %r — %s", rec, exc)

        return added

    @staticmethod
    def _fetch_lastfm(artist: str, title: str) -> tuple[str, str, list[str]]:
        """Synchronous Last.fm call — runs in executor."""
        if not artist:
            return title, artist, []
        tags = lastfm.get_top_tags(artist, title)
        info = lastfm.get_track_info(artist, title)
        if info:
            lf_title  = info.get("name", title)
            lf_artist = info.get("artist", {}).get("name", artist) if isinstance(info.get("artist"), dict) else artist
            return lf_title, lf_artist, tags
        return title, artist, tags


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TrackCollector(bot))
