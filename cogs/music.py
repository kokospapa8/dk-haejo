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
from yt_dlp.utils import DownloadError

from utils import history as hist
from utils import playlist as plist
from utils.music_queue import MusicQueue, RepeatMode, Song
from utils.youtube import FFMPEG_OPTIONS, get_stream_url, search_youtube, search_youtube_multi

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
        """Fetch a fresh stream URL then start playback in the guild's voice channel.

        Stream URLs are fetched here (not at queue time) so they never expire
        even if the song has been waiting in the queue for hours.
        """
        vc = guild.voice_client
        if not vc:
            return

        # Fetch a fresh stream URL right before playback
        try:
            stream_url = await get_stream_url(song.webpage_url)
        except DownloadError as exc:
            log.error("Stream URL fetch failed for %r: %s", song.title, exc)
            ch = self._text_channels.get(guild.id)
            if ch:
                await ch.send(
                    f"⚠️ **{song.title}** 스트림 URL을 가져오지 못했습니다. "
                    "다음 곡으로 넘어갑니다."
                )
            await self._play_next(guild)
            return
        except Exception as exc:
            log.exception("Unexpected error fetching stream URL for %r", song.title)
            await self._play_next(guild)
            return

        queue = self._get_queue(guild.id)
        source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
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

        # Persist to playback history (non-blocking — fast file write)
        try:
            hist.add_song(guild.id, song)
        except Exception:
            log.exception("history: failed to save song %r", song.title)

        # Update bot Activity status → shows "Listening to <title>" next to bot name
        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=song.title,
            )
        )

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
            await self.bot.change_presence(activity=None)  # clear "Listening to" status
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

        # Search YouTube for metadata (fast — no stream URL fetched yet)
        try:
            meta = await search_youtube(query)
        except Exception as exc:
            log.exception("YouTube search failed for query %r", query)
            err = str(exc)

            # 검색 실패 시 봇이 아무것도 재생 안 하고 있으면 즉시 퇴장
            vc = guild.voice_client
            if vc and not vc.is_playing() and not vc.is_paused():
                queue = self._get_queue(guild.id)
                if not queue.queue:  # 큐도 비어있으면 나감
                    self._cancel_idle_timer(guild.id)
                    await vc.disconnect()

            if "Sign in to confirm" in err or "not a bot" in err:
                return (
                    "❌ YouTube가 봇으로 감지했습니다. "
                    "잠시 후 다시 시도하거나 관리자에게 문의해 주세요."
                )
            if "no longer supported" in err.lower():
                return (
                    "❌ YouTube 클라이언트 버전 문제가 발생했습니다. "
                    "관리자에게 문의해 주세요."
                )
            if "Video unavailable" in err or "not available" in err.lower():
                return f"❌ **{query}** — 해당 영상을 재생할 수 없습니다 (지역 제한 또는 삭제된 영상)."
            if "Private video" in err:
                return "❌ 비공개 영상은 재생할 수 없습니다."
            short = err.split("\n")[0][:200]
            return f"❌ 검색 실패: {short}"

        # Build Song — no stream URL stored; fetched fresh in _play_audio()
        song = Song(
            title=meta["title"],
            webpage_url=meta["webpage_url"],
            duration=meta["duration"],
            thumbnail=meta.get("thumbnail"),
            requested_by=member.display_name,
            video_id=meta.get("video_id", ""),
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

    async def play_songs(
        self,
        guild: discord.Guild,
        member: discord.Member,
        queries: list[str],
        text_channel: Optional[discord.TextChannel] = None,
    ) -> str:
        """Search multiple songs in parallel and add them all to the queue.

        The first result starts playing immediately if nothing is currently playing;
        the rest are enqueued. Searches run concurrently so the total wait time
        is roughly equal to the slowest single search.
        """
        if text_channel:
            self._text_channels[guild.id] = text_channel

        # Auto-join if not connected
        if not guild.voice_client:
            ok, msg = await self.join_channel(member)
            if not ok:
                return msg

        # Search all queries in parallel
        log.info("play_songs: searching %d queries in parallel", len(queries))
        results = await asyncio.gather(
            *[search_youtube(q) for q in queries],
            return_exceptions=True,
        )

        songs: list[Song] = []
        failed: list[str] = []
        for q, result in zip(queries, results):
            if isinstance(result, Exception):
                log.warning("play_songs: search failed for %r: %s", q, result)
                failed.append(q)
                continue
            songs.append(Song(
                title=result["title"],
                webpage_url=result["webpage_url"],
                duration=result["duration"],
                thumbnail=result.get("thumbnail"),
                requested_by=member.display_name,
                video_id=result.get("video_id", ""),
            ))

        if not songs:
            return "❌ 검색된 곡이 없습니다."

        queue = self._get_queue(guild.id)
        vc = guild.voice_client
        play_first = not vc.is_playing() and not vc.is_paused()

        lines: list[str] = []
        for i, song in enumerate(songs):
            dur = self._format_duration(song.duration)
            if i == 0 and play_first:
                queue.current = song
                await self._play_audio(guild, song)
                lines.append(f"▶️ **{song.title}** `[{dur}]` 재생 중")
            else:
                await queue.add(song)
                pos = len(queue.queue)
                lines.append(f"✅ **{song.title}** `[{dur}]` → 큐 #{pos}")

        if failed:
            failed_str = ", ".join(f"`{q}`" for q in failed)
            lines.append(f"\n⚠️ 검색 실패: {failed_str}")

        return "\n".join(lines)

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
        await self.bot.change_presence(activity=None)  # clear "Listening to" status
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

    async def remove_from_queue(self, guild: discord.Guild, indices: list[int]) -> str:
        """Remove songs by 1-based position numbers (user-facing). Accepts multiple indices."""
        queue = self._get_queue(guild.id)
        zero_based = [i - 1 for i in indices]
        removed = await queue.remove_multiple(zero_based)

        if not removed:
            out_of_range = [i for i in indices if i < 1 or i > len(queue.queue) + len(removed)]
            return (
                f"❌ 해당 번호의 곡을 찾을 수 없습니다. "
                f"(큐에 {len(queue.queue)}곡 있음)"
            )

        if len(removed) == 1:
            return f"🗑 **{removed[0].title}** 을(를) 큐에서 제거했습니다."

        lines = [f"🗑 {len(removed)}곡을 큐에서 제거했습니다:"]
        lines += [f"  • **{s.title}**" for s in removed]
        return "\n".join(lines)

    async def add_from_search(
        self,
        guild: discord.Guild,
        member: discord.Member,
        search_results: list[dict],
        indices: list[int],          # 1-based
        text_channel: Optional[discord.TextChannel] = None,
    ) -> str:
        """Add songs selected by index from a prior search_results list to the queue."""
        if not search_results:
            return "❌ 검색 결과가 없습니다. 먼저 검색해 주세요."

        if text_channel:
            self._text_channels[guild.id] = text_channel

        if not guild.voice_client:
            ok, msg = await self.join_channel(member)
            if not ok:
                return msg

        selected = [
            search_results[i - 1]
            for i in indices
            if 1 <= i <= len(search_results)
        ]
        if not selected:
            return f"❌ 올바른 번호를 선택해 주세요. (1–{len(search_results)})"

        songs = [
            Song(
                title=r["title"],
                webpage_url=r["webpage_url"],
                duration=r["duration"],
                thumbnail=r.get("thumbnail"),
                requested_by=member.display_name,
                video_id=r.get("video_id", ""),
            )
            for r in selected
        ]

        queue = self._get_queue(guild.id)
        vc = guild.voice_client
        play_first = not vc.is_playing() and not vc.is_paused()

        lines: list[str] = []
        for i, song in enumerate(songs):
            dur = self._format_duration(song.duration)
            if i == 0 and play_first:
                queue.current = song
                await self._play_audio(guild, song)
                lines.append(f"▶️ **{song.title}** `[{dur}]` 재생 중")
            else:
                await queue.add(song)
                pos = len(queue.queue)
                lines.append(f"✅ **{song.title}** `[{dur}]` → 큐 #{pos}")

        return "\n".join(lines)

    # ── playlist methods ──────────────────────────────────────────────────────

    async def add_to_playlist(
        self,
        guild: discord.Guild,
        member: discord.Member,
        query: Optional[str] = None,
    ) -> str:
        """Add a song to the requesting member's OWN playlist only.

        If *query* is None, adds the currently playing song.
        Otherwise searches YouTube for *query* and adds the first result.
        """
        user_id = str(member.id)
        username = member.display_name

        if query:
            # Search YouTube for the requested song
            try:
                meta = await search_youtube(query)
            except Exception as exc:
                return f"❌ 검색 실패: {str(exc)[:200]}"
            song_data = meta
        else:
            # Add currently playing song
            queue = self._get_queue(guild.id)
            if not queue.current:
                return "⚠️ 현재 재생 중인 곡이 없습니다."
            song_data = queue.current

        already_existed, total = plist.add_song(guild.id, user_id, username, song_data)
        title = (
            song_data.get("title") if isinstance(song_data, dict)
            else getattr(song_data, "title", "?")
        )

        if already_existed:
            return f"🔄 **{title}** 은(는) 이미 플레이리스트에 있어서 맨 뒤로 이동했습니다. (총 {total}곡)"
        if total == plist.MAX_PER_USER and not already_existed:
            # song was NOT added (cap reached)
            return (
                f"❌ 플레이리스트가 가득 찼습니다 ({plist.MAX_PER_USER}곡). "
                "곡을 먼저 삭제해 주세요."
            )
        return f"✅ **{title}** 을(를) 내 플레이리스트에 추가했습니다. (총 {total}곡)"

    async def remove_from_playlist(
        self,
        guild: discord.Guild,
        member: discord.Member,
        indices: list[int],
    ) -> str:
        """Remove songs from the requesting member's OWN playlist by 1-based indices."""
        user_id = str(member.id)
        zero_based = [i - 1 for i in indices]
        removed = plist.remove_songs(guild.id, user_id, zero_based)

        if not removed:
            _, songs = plist.get_playlist(guild.id, user_id)
            return f"❌ 해당 번호의 곡을 찾을 수 없습니다. (플레이리스트에 {len(songs)}곡 있음)"

        if len(removed) == 1:
            return f"🗑 **{removed[0]['title']}** 을(를) 플레이리스트에서 제거했습니다."

        lines = [f"🗑 {len(removed)}곡을 플레이리스트에서 제거했습니다:"]
        lines += [f"  • **{s['title']}**" for s in removed]
        return "\n".join(lines)

    def get_playlist_for_display(
        self,
        guild: discord.Guild,
        member: discord.Member,
        target_username: Optional[str] = None,
    ) -> tuple[str, list[dict]]:
        """Return (display_name, songs) for viewing.

        If *target_username* is None or matches the member's own name,
        returns the member's own playlist.  Otherwise does a name search.
        Returns ("", []) when the target is not found.
        """
        own_name = member.display_name

        if not target_username or target_username.lower() == own_name.lower():
            username, songs = plist.get_playlist(guild.id, str(member.id))
            return (username or own_name), songs

        # Someone else's playlist
        _, username, songs = plist.find_by_username(guild.id, target_username)
        if username is None:
            return "", []
        return username, songs

    def get_current_song(self, guild: discord.Guild) -> Optional[Song]:
        """Return the currently playing/paused song, or None."""
        return self._get_queue(guild.id).current

    def get_history(self, guild: discord.Guild, limit: int = 20) -> list[dict]:
        """Return recent playback history for this guild."""
        return hist.get_history(guild.id, limit)

    async def remove_by_title(self, guild: discord.Guild, title: str) -> str:
        """Remove the first queue entry whose title contains *title* (case-insensitive)."""
        queue = self._get_queue(guild.id)
        song = await queue.remove_by_title(title)
        if song:
            return f"🗑 **{song.title}** 을(를) 큐에서 제거했습니다."
        return f"❌ `{title}` 와(과) 일치하는 곡을 큐에서 찾을 수 없습니다."

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
        await self.bot.change_presence(activity=None)  # clear "Listening to" status
        return "👋 음성 채널에서 나갔습니다."


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
