"""
Now Playing Panel — persistent embed with interactive controls.

One message per guild, continuously edited in-place.
Auto-reposts when pushed up by REPOST_THRESHOLD unrelated messages.
Progress bar refreshes every PROGRESS_INTERVAL seconds while playing.

Button layout (row 0):   ⏮️  ⏯️  ⏭️  🔁  ⏹️
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

from utils.music_queue import RepeatMode

log = logging.getLogger(__name__)

REPOST_THRESHOLD = 5     # non-bot messages before auto-repost
PROGRESS_INTERVAL = 5    # seconds between progress bar updates


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _bar(elapsed: float, total: float, width: int = 14) -> str:
    if not total or total <= 0:
        return "▰" * width
    ratio = min(1.0, max(0.0, elapsed / total))
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


# ── ephemeral queue-remove select ─────────────────────────────────────────────

class _QueueRemoveSelect(discord.ui.Select):
    def __init__(self, panel: "MusicPanel", guild: discord.Guild, queue_items: list) -> None:
        self._panel = panel
        self._guild = guild
        options = [
            discord.SelectOption(
                label=f"{i}. {s.title[:80]}",
                value=str(i),
                description=_fmt(s.duration),
            )
            for i, s in enumerate(queue_items[:25], 1)
        ]
        super().__init__(
            placeholder="삭제할 곡을 선택하세요 (복수 선택 가능)",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        music = self._panel.bot.cogs.get("Music")
        if not music:
            return
        indices = sorted(int(v) for v in self.values)
        result = await music.remove_from_queue(self._guild, indices)  # type: ignore[union-attr]
        await interaction.followup.send(result, ephemeral=True)


class _QueueRemoveView(discord.ui.View):
    def __init__(self, panel: "MusicPanel", guild: discord.Guild, queue_items: list) -> None:
        super().__init__(timeout=60)
        self.add_item(_QueueRemoveSelect(panel, guild, queue_items))


# ── persistent button view ────────────────────────────────────────────────────

class MusicControlView(discord.ui.View):
    """Control buttons for the Now Playing panel. timeout=None keeps them alive forever."""

    def __init__(self, panel: "MusicPanel") -> None:
        super().__init__(timeout=None)
        self._panel = panel

    def _music(self) -> Optional[object]:
        return self._panel.bot.cogs.get("Music")

    # ── row 0 buttons ─────────────────────────────────────────────────────────

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="mp_prev", row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Restart the current song."""
        await interaction.response.defer()
        music = self._music()
        if not music or not interaction.guild:
            return
        await music.restart_song(interaction.guild)  # type: ignore[union-attr]

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="mp_playpause", row=0)
    async def btn_playpause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        music = self._music()
        guild = interaction.guild
        if not music or not guild:
            return
        vc = guild.voice_client
        if vc and vc.is_paused():
            await music.resume(guild)  # type: ignore[union-attr]
        elif vc and vc.is_playing():
            await music.pause(guild)  # type: ignore[union-attr]

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="mp_skip", row=0)
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        music = self._music()
        if music and interaction.guild:
            await music.skip(interaction.guild)  # type: ignore[union-attr]

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="mp_repeat", row=0)
    async def btn_repeat(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Cycle repeat mode: OFF → single → queue → OFF."""
        await interaction.response.defer()
        music = self._music()
        if not music or not interaction.guild:
            return
        queue = music._get_queue(interaction.guild.id)  # type: ignore[union-attr]
        cycle = {RepeatMode.OFF: "single", RepeatMode.SINGLE: "queue", RepeatMode.QUEUE: "off"}
        await music.set_repeat(interaction.guild, cycle[queue.repeat_mode])  # type: ignore[union-attr]

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="mp_stop", row=0)
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        music = self._music()
        if music and interaction.guild:
            await music.stop(interaction.guild)  # type: ignore[union-attr]

    @discord.ui.button(emoji="🗑️", label="큐 삭제", style=discord.ButtonStyle.secondary, custom_id="mp_queue_remove", row=1)
    async def btn_queue_remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        music = self._music()
        guild = interaction.guild
        if not music or not guild:
            await interaction.response.send_message("❌ 오류가 발생했습니다.", ephemeral=True)
            return
        queue = music._get_queue(guild.id)  # type: ignore[union-attr]
        if not queue.queue:
            await interaction.response.send_message("📋 큐가 비어있습니다.", ephemeral=True)
            return
        view = _QueueRemoveView(self._panel, guild, list(queue.queue))
        await interaction.response.send_message("삭제할 곡을 선택하세요:", view=view, ephemeral=True)


# ── panel cog ─────────────────────────────────────────────────────────────────

def _load_music_channel_ids() -> set[int]:
    import os
    raw = os.getenv("MUSIC_CHANNEL_IDS", "")
    return {int(p) for p in raw.split(",") if p.strip().isdigit()}


class MusicPanel(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._music_channel_ids: set[int] = _load_music_channel_ids()
        # guild_id → {"channel": TextChannel, "message": Message|None, "repost_count": int}
        self._panels: dict[int, dict] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._view = MusicControlView(self)
        bot.add_view(self._view)   # register for persistence across restarts

    def cog_unload(self) -> None:
        self._progress_loop.cancel()

    # ── event listeners ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_music_state_change(self, guild: discord.Guild) -> None:
        """Fired by Music cog whenever playback state changes."""
        await self.refresh(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Count non-bot messages in the panel channel; auto-repost when panel is pushed up."""
        if message.author.bot or not message.guild:
            return
        panel = self._panels.get(message.guild.id)
        if not panel or panel.get("message") is None:
            return
        if message.channel.id != panel["channel"].id:
            return
        panel["repost_count"] = panel.get("repost_count", 0) + 1
        if panel["repost_count"] >= REPOST_THRESHOLD:
            await self.refresh(message.guild, force_repost=True)

    # ── progress bar background task ──────────────────────────────────────────

    @tasks.loop(seconds=PROGRESS_INTERVAL)
    async def _progress_loop(self) -> None:
        for guild_id in list(self._panels):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            vc = guild.voice_client
            if vc and vc.is_playing():
                try:
                    await self._edit_panel(guild)
                except Exception as exc:
                    log.warning("panel: progress update failed guild=%s: %s", guild_id, exc)

    @_progress_loop.before_loop
    async def _before_progress(self) -> None:
        await self.bot.wait_until_ready()

    # ── public API ────────────────────────────────────────────────────────────

    def _resolve_panel_channel(self, guild: discord.Guild, music) -> Optional[discord.TextChannel]:
        """Return a panel channel that is within MUSIC_CHANNEL_IDS.

        Prefers the channel where the last command came from (if it's a music
        channel); falls back to the first reachable music channel.
        """
        candidate: Optional[discord.TextChannel] = music._text_channels.get(guild.id)  # type: ignore[union-attr]
        if candidate and candidate.id in self._music_channel_ids:
            return candidate
        for cid in self._music_channel_ids:
            ch = self.bot.get_channel(cid)
            if isinstance(ch, discord.TextChannel) and ch.guild.id == guild.id:
                return ch
        return None

    async def refresh(self, guild: discord.Guild, *, force_repost: bool = False) -> None:
        """Create or update the panel for this guild."""
        music = self.bot.cogs.get("Music")
        if not music:
            return

        channel = self._resolve_panel_channel(guild, music)
        if not channel:
            return

        panel = self._panels.get(guild.id)

        # Channel changed or forced repost → delete old and start fresh
        if force_repost or (panel and panel["channel"].id != channel.id):
            await self._delete_panel(guild.id)
            panel = None

        if panel is None:
            self._panels[guild.id] = {
                "channel": channel,
                "message": None,
                "repost_count": 0,
            }

        await self._edit_panel(guild)

        if not self._progress_loop.is_running():
            self._progress_loop.start()

    # ── internal ──────────────────────────────────────────────────────────────

    async def _edit_panel(self, guild: discord.Guild) -> None:
        music = self.bot.cogs.get("Music")
        panel = self._panels.get(guild.id)
        if not music or not panel:
            return

        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        if lock.locked():
            return  # skip if an edit is already in progress for this guild

        async with lock:
            embed = self._build_embed(guild, music)
            channel: discord.TextChannel = panel["channel"]

            if panel["message"] is None:
                try:
                    msg = await channel.send(embed=embed, view=self._view)
                    panel["message"] = msg
                    panel["repost_count"] = 0
                except discord.HTTPException as exc:
                    log.warning("panel: send failed guild=%s: %s", guild.id, exc)
            else:
                try:
                    await panel["message"].edit(embed=embed, view=self._view)
                except discord.NotFound:
                    panel["message"] = None
                    panel["repost_count"] = 0
                    try:
                        msg = await channel.send(embed=embed, view=self._view)
                        panel["message"] = msg
                    except discord.HTTPException as exc:
                        log.warning("panel: recreate failed guild=%s: %s", guild.id, exc)
                except discord.HTTPException as exc:
                    log.warning("panel: edit failed guild=%s: %s", guild.id, exc)

    async def _delete_panel(self, guild_id: int) -> None:
        panel = self._panels.pop(guild_id, None)
        if panel and panel.get("message"):
            try:
                await panel["message"].delete()
            except discord.HTTPException:
                pass

    def _build_embed(self, guild: discord.Guild, music) -> discord.Embed:  # type: ignore[type-arg]
        queue = music._get_queue(guild.id)
        vc = guild.voice_client
        embed = discord.Embed(color=0x1DB954)

        if not queue.current:
            embed.title = "🎵 재생 없음"
            embed.description = "재생 중인 곡이 없습니다.\n음악을 요청해 주세요!"
            return embed

        song = queue.current
        is_paused = bool(vc and vc.is_paused())
        status = "⏸️" if is_paused else "▶️"
        elapsed = queue.get_elapsed()

        embed.title = f"{status}  {song.title}"
        embed.description = (
            f"요청: **{song.requested_by}**\n"
            f"`{_bar(elapsed, song.duration)}` {_fmt(elapsed)} / {_fmt(song.duration)}"
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)

        # Queue preview
        if queue.queue:
            lines = [
                f"`{i}.` **{s.title}** `[{_fmt(s.duration)}]`"
                for i, s in enumerate(queue.queue[:5], 1)
            ]
            if len(queue.queue) > 5:
                lines.append(f"… 외 {len(queue.queue) - 5}곡")
            embed.add_field(
                name=f"📋 다음 곡 ({len(queue.queue)}곡)",
                value="\n".join(lines),
                inline=False,
            )

        repeat_labels = {
            RepeatMode.OFF:    "🔁 반복 없음",
            RepeatMode.SINGLE: "🔂 한 곡 반복",
            RepeatMode.QUEUE:  "🔁 전체 반복",
        }
        embed.set_footer(
            text=f"{repeat_labels[queue.repeat_mode]} │ 🔊 {int(queue.volume * 100)}%"
        )
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicPanel(bot))
