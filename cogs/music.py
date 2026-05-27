"""
Music Cog – handles per-guild voice audio playback and queue management.
All public methods return plain text (or a discord.Embed) so they can be
called from both the LLM listener and traditional slash commands.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from utils.music_queue import MusicQueue, RepeatMode, Song
from utils.youtube import FFMPEG_OPTIONS, search_youtube

log = logging.getLogger(__name__)

_IDLE_TIMEOUT = 5 * 60  # 5분 동안 음악 없으면 자동 퇴장 (초)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._queues: dict[int, MusicQueue] = {}  # guild_id → MusicQueue
        # 명령어를 받은 텍스트 채널 저장 → 재생 중 에러 발생 시 알림용
        self._text_channels: dict[int, discord.TextChannel] = {}
        # 유휴 타임아웃 태스크 (guild_id → Task)
        self._idle_tasks: dict[int, asyncio.Task] = {}

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get_queue(self, guild_id: int) -> MusicQueue:
        if guild_id not in self._queues:
            self._queues[guild_id] = MusicQueue()
        return self._queues[guild_id]

    def _format_duration(self, seconds: int) -> str:
        if not seconds:
            return "LIVE"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    async def _play_audio(self, guild: discord.Guild, song: Song) -> None:
        """Start streaming *song* in the guild's voice channel."""
        vc = guild.voice_client
        if not vc:
            return

        queue = self._get_queue(guild.id)
        source = discord.FFmpegPCMAudio(song.url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=queue.volume)

        def _after(error: Optional[Exception]) -> None:
            if error:
                log.error("Playback error in guild %s: %s", guild.id, error)
                ch = self._text_channels.get(guild.id)
                if ch:
                    asyncio.run_coroutine_threadsafe(
                        ch.send(f"⚠️ 재생 중 오류가 발생했습니다: `{error}`"),
                        self.bot.loop,
                    )
            asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)

        self._cancel_idle_timer(guild.id)  # 재생 시작 → 타이머 취소
        vc.play(source, after=_after)

    async def _play_next(self, guild: discord.Guild) -> None:
        """Called automatically after a song finishes."""
        queue = self._get_queue(guild.id)
        next_song = await queue.next()
        if next_song:
            await self._play_audio(guild, next_song)
        else:
            # 큐 소진 → 유휴 타임아웃 시작
            self._start_idle_timer(guild)

    # ── idle timeout ──────────────────────────────────────────────────────────

    def _start_idle_timer(self, guild: discord.Guild) -> None:
        """Start (or restart) the idle-leave timer for this guild."""
        self._cancel_idle_timer(guild.id)
        task = asyncio.ensure_future(self._idle_leave(guild))
        self._idle_tasks[guild.id] = task

    def _cancel_idle_timer(self, guild_id: int) -> None:
        task = self._idle_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    async def _idle_leave(self, guild: discord.Guild) -> None:
        """Wait _IDLE_TIMEOUT seconds then leave if still idle."""
        await asyncio.sleep(_IDLE_TIMEOUT)
        vc = guild.voice_client
        if not vc:
            return
        # 타임아웃 시점에도 재생 중이 아니면 퇴장
        if not vc.is_playing() and not vc.is_paused():
            log.info("Idle timeout — leaving voice in guild %s", guild.id)
            queue = self._get_queue(guild.id)
            queue.clear()
            await vc.disconnect()
            ch = self._text_channels.get(guild.id)
            if ch:
                await ch.send("😴 5분 동안 음악이 없어서 음성 채널을 나갔습니다.")

    # ── public methods (called by LLM listener) ───────────────────────────────

    async def join_channel(self, member: discord.Member) -> tuple[bool, str]:
        """Join the voice channel the member is in."""
        if not member.voice:
            return False, "⚠️ 먼저 음성 채널에 입장해 주세요."
        channel = member.voice.channel
        guild = member.guild
        if guild.voice_client:
            await guild.voice_client.move_to(channel)
        else:
            # self_deaf=True: 봇이 음성 채널에서 다른 사람 목소리를 듣지 않음
            await channel.connect(self_deaf=True)
        return True, f"🎵 **{channel.name}** 채널에 입장했습니다."

    async def play_song(
        self,
        guild: discord.Guild,
        member: discord.Member,
        query: str,
        text_channel: Optional[discord.TextChannel] = None,
    ) -> str:
        # 텍스트 채널 저장 (재생 중 에러 알림용)
        if text_channel:
            self._text_channels[guild.id] = text_channel

        # Auto-join if not connected
        if not guild.voice_client:
            ok, msg = await self.join_channel(member)
            if not ok:
                return msg

        # Fetch song info from YouTube (runs in thread pool)
        try:
            info = await search_youtube(query)
        except Exception as exc:
            log.exception("YouTube search failed for query %r", query)
            err = str(exc)
            if "Sign in to confirm" in err or "not a bot" in err:
                return (
                    "❌ YouTube가 봇으로 감지했습니다. "
                    "잠시 후 다시 시도하거나 관리자에게 문의해 주세요."
                )
            if "Video unavailable" in err or "not available" in err.lower():
                return f"❌ **{query}** — 해당 영상을 재생할 수 없습니다 (지역 제한 또는 삭제된 영상)."
            if "Private video" in err:
                return "❌ 비공개 영상은 재생할 수 없습니다."
            # 그 외 에러: 짧게 요약해서 보여줌
            short = err.split("\n")[0][:200]
            return f"❌ 검색 실패: {short}"

        song = Song(
            title=info["title"],
            url=info["url"],
            webpage_url=info["webpage_url"],
            duration=info["duration"],
            thumbnail=info.get("thumbnail"),
            requested_by=member.display_name,
        )
        queue = self._get_queue(guild.id)
        vc = guild.voice_client

        if not vc.is_playing() and not vc.is_paused():
            queue.current = song
            await self._play_audio(guild, song)
            dur = self._format_duration(song.duration)
            return f"▶️ **{song.title}** `[{dur}]` 재생 중"
        else:
            await queue.add(song)
            pos = len(queue.queue)
            dur = self._format_duration(song.duration)
            return f"✅ **{song.title}** `[{dur}]` → 큐 #{pos} 추가됨"

    async def pause(self, guild: discord.Guild) -> str:
        vc = guild.voice_client
        if not vc or not vc.is_playing():
            return "⚠️ 현재 재생 중인 곡이 없습니다."
        vc.pause()
        return "⏸ 일시정지했습니다."

    async def resume(self, guild: discord.Guild) -> str:
        vc = guild.voice_client
        if not vc or not vc.is_paused():
            return "⚠️ 일시정지된 곡이 없습니다."
        vc.resume()
        return "▶️ 재생을 재개합니다."

    async def skip(self, guild: discord.Guild) -> str:
        vc = guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            return "⚠️ 현재 재생 중인 곡이 없습니다."
        queue = self._get_queue(guild.id)
        title = queue.current.title if queue.current else "현재 곡"
        vc.stop()  # triggers _after → _play_next
        return f"⏭ **{title}** 건너뜁니다."

    async def stop(self, guild: discord.Guild) -> str:
        vc = guild.voice_client
        if not vc:
            return "⚠️ 봇이 음성 채널에 없습니다."
        queue = self._get_queue(guild.id)
        queue.clear()
        self._cancel_idle_timer(guild.id)
        vc.stop()
        self._start_idle_timer(guild)  # 정지 후 5분 타이머 시작
        return "⏹ 재생을 멈추고 큐를 비웠습니다."

    def view_queue(self, guild: discord.Guild) -> discord.Embed:
        queue = self._get_queue(guild.id)
        vc = guild.voice_client

        embed = discord.Embed(title="🎵 재생 큐", color=0x1DB954)

        # ── currently playing ──
        if queue.current:
            status = "⏸ 일시정지" if (vc and vc.is_paused()) else "▶️ 재생 중"
            dur = self._format_duration(queue.current.duration)
            embed.add_field(
                name=status,
                value=f"**{queue.current.title}** `[{dur}]`\n요청: {queue.current.requested_by}",
                inline=False,
            )
        else:
            embed.add_field(name="현재 재생 중", value="없음", inline=False)

        # ── upcoming ──
        if queue.queue:
            lines = []
            for i, song in enumerate(queue.queue[:10], 1):
                dur = self._format_duration(song.duration)
                lines.append(f"`{i}.` **{song.title}** `[{dur}]`")
            if len(queue.queue) > 10:
                lines.append(f"… 외 {len(queue.queue) - 10}곡")
            embed.add_field(
                name=f"대기 중 ({len(queue.queue)}곡)",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="대기 중", value="없음", inline=False)

        # ── footer ──
        repeat_text = {
            RepeatMode.OFF: "🔁 반복 없음",
            RepeatMode.SINGLE: "🔂 한 곡 반복",
            RepeatMode.QUEUE: "🔁 전체 반복",
        }
        embed.set_footer(
            text=f"{repeat_text[queue.repeat_mode]} │ 볼륨: {int(queue.volume * 100)}%"
        )
        return embed

    async def remove_from_queue(self, guild: discord.Guild, index: int) -> str:
        """index is 1-based (user-facing)."""
        queue = self._get_queue(guild.id)
        song = await queue.remove(index - 1)
        if song:
            return f"🗑 **{song.title}** 을(를) 큐에서 제거했습니다."
        return f"❌ {index}번 곡을 찾을 수 없습니다. (큐에 {len(queue.queue)}곡 있음)"

    async def set_repeat(self, guild: discord.Guild, mode: str) -> str:
        queue = self._get_queue(guild.id)
        mode_map = {
            "off": RepeatMode.OFF,
            "single": RepeatMode.SINGLE,
            "queue": RepeatMode.QUEUE,
        }
        if mode not in mode_map:
            return "❌ 올바른 반복 모드: `off` / `single` / `queue`"
        queue.repeat_mode = mode_map[mode]
        labels = {"off": "🔁 반복 없음", "single": "🔂 한 곡 반복", "queue": "🔁 전체 반복"}
        return f"반복 모드: **{labels[mode]}**"

    async def set_volume(self, guild: discord.Guild, level: int) -> str:
        level = max(0, min(100, level))
        queue = self._get_queue(guild.id)
        queue.volume = level / 100
        vc = guild.voice_client
        if vc and vc.source:
            vc.source.volume = queue.volume
        return f"🔊 볼륨: **{level}%**"

    async def leave(self, guild: discord.Guild) -> str:
        vc = guild.voice_client
        if not vc:
            return "⚠️ 봇이 음성 채널에 없습니다."
        queue = self._get_queue(guild.id)
        queue.clear()
        self._cancel_idle_timer(guild.id)
        await vc.disconnect()
        return "👋 음성 채널에서 나갔습니다."


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
