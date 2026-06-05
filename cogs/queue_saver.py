"""
Queue Saver — persists the current queue on every state change and
restores it automatically when the bot restarts.

Restore flow:
  1. on_ready → for each guild, if saved state exists → populate queue.queue
  2. Bot is NOT in voice channel after restart, so queue waits silently
  3. A notification is sent to the last active text channel (or first music channel)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord
from discord.ext import commands

from utils.music_queue import Song
from utils import queue_persist

log = logging.getLogger(__name__)


def _load_channel_ids() -> set[int]:
    raw = os.getenv("MUSIC_CHANNEL_IDS", "")
    return {int(p) for p in raw.split(",") if p.strip().isdigit()}


class QueueSaver(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._music_channel_ids = _load_channel_ids()

    # ── save on every queue state change ─────────────────────────────────────

    @commands.Cog.listener()
    async def on_music_state_change(self, guild: discord.Guild) -> None:
        music = self.bot.cogs.get("Music")
        if not music:
            return
        queue = music._get_queue(guild.id)  # type: ignore[union-attr]
        ch: Optional[discord.TextChannel] = music._text_channels.get(guild.id)  # type: ignore[union-attr]
        vc = guild.voice_client
        vc_id = vc.channel.id if (vc and vc.channel) else None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, queue_persist.save, guild.id, queue,
            ch.id if ch else None, vc_id,
        )

    # ── restore on startup ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await asyncio.sleep(2)   # let other cogs initialize first
        for guild in self.bot.guilds:
            await self._restore(guild)

    async def _restore(self, guild: discord.Guild) -> None:
        state = await asyncio.get_running_loop().run_in_executor(
            None, queue_persist.load, guild.id
        )
        if not state or not state.get("songs"):
            return

        music = self.bot.cogs.get("Music")
        if not music:
            return

        queue = music._get_queue(guild.id)  # type: ignore[union-attr]

        # Skip if already playing (e.g. hot-reload without restart)
        if queue.current or queue.queue:
            return

        songs = [
            Song(
                title=s["title"],
                webpage_url=s["webpage_url"],
                duration=s.get("duration", 0),
                thumbnail=s.get("thumbnail"),
                requested_by=s.get("requested_by", "?"),
                video_id=s.get("video_id", ""),
            )
            for s in state["songs"]
        ]
        queue.queue.extend(songs)

        from utils.music_queue import RepeatMode
        try:
            queue.repeat_mode = RepeatMode(state.get("repeat_mode", "off"))
        except ValueError:
            pass
        queue.volume = float(state.get("volume", 0.5))

        log.info("queue_saver: restored %d songs for guild %s", len(songs), guild.id)

        ch = self._resolve_channel(guild, state.get("text_channel_id"))

        # ── auto-reconnect to voice channel and resume playback ───────────────
        vc_id = state.get("voice_channel_id")
        log.info(
            "queue_saver [restore] guild=%s  songs=%d  vc_id=%s  repeat=%s  volume=%.1f",
            guild.id, len(songs), vc_id, state.get("repeat_mode"), queue.volume,
        )
        for i, s in enumerate(songs):
            log.info("queue_saver [restore]   [%d] %s", i + 1, s.title)

        auto_playing = False
        if vc_id:
            vc_channel = guild.get_channel(vc_id)
            log.info(
                "queue_saver [restore] vc lookup: id=%s  found=%s  type=%s",
                vc_id, vc_channel, type(vc_channel).__name__ if vc_channel else "None",
            )
            if isinstance(vc_channel, discord.VoiceChannel):
                try:
                    log.info("queue_saver [restore] connecting to #%s …", vc_channel.name)
                    await vc_channel.connect()
                    log.info("queue_saver [restore] connected  guild_vc=%s", guild.voice_client)
                    # Register the text channel so _play_audio can send messages
                    if ch:
                        music._text_channels[guild.id] = ch  # type: ignore[union-attr]
                    await asyncio.sleep(1)   # let the voice connection stabilise
                    log.info("queue_saver [restore] calling _play_next …")
                    await music._play_next(guild)  # type: ignore[union-attr]
                    auto_playing = True
                    log.info(
                        "queue_saver [restore] ✅ auto-resumed in #%s (guild %s)",
                        vc_channel.name, guild.id,
                    )
                except Exception as exc:
                    log.warning(
                        "queue_saver [restore] ❌ auto-reconnect FAILED guild=%s: %s",
                        guild.id, exc, exc_info=True,
                    )
        else:
            log.info("queue_saver [restore] no voice_channel_id in saved state — manual play required")

        if ch:
            if auto_playing:
                first = songs[0].title if songs else "?"
                await ch.send(
                    f"🔄 재시작 후 **{len(songs)}곡** 복구 — "
                    f"▶️ **{first}** 재생을 재개합니다."
                )
            else:
                await ch.send(
                    f"🔄 재시작 후 이전 대기열 **{len(songs)}곡**을 복구했습니다.\n"
                    f"재생하려면 **'틀어줘'** 또는 **'play'** 라고 해주세요!"
                )

    def _resolve_channel(
        self, guild: discord.Guild, saved_ch_id: int | None
    ) -> Optional[discord.TextChannel]:
        if saved_ch_id:
            ch = self.bot.get_channel(saved_ch_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        for cid in self._music_channel_ids:
            ch = self.bot.get_channel(cid)
            if isinstance(ch, discord.TextChannel) and ch.guild.id == guild.id:
                return ch
        return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueSaver(bot))
