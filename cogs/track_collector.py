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

from utils import lastfm, user_tracks

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
