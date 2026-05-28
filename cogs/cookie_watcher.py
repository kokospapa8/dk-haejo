"""
Cookie Watcher Cog — monitors YouTube cookie expiry and alerts via Discord.

Parses cookies.txt on startup and every 24 hours.  Sends an alert embed
to every channel in MUSIC_CHANNEL_IDS when cookies are expired or about
to expire.

Config (env vars):
  MUSIC_CHANNEL_IDS   Comma-separated Discord channel IDs (shared with music cog).
                      Alerts are broadcast to all of these channels.
  COOKIE_WARN_DAYS    Days threshold for "near expiry" warning (default: 7).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from utils import cookie_monitor

log = logging.getLogger(__name__)


def _load_music_channel_ids() -> list[int]:
    raw = os.getenv("MUSIC_CHANNEL_IDS", "").strip()
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


_MUSIC_CHANNEL_IDS: list[int] = _load_music_channel_ids()


class CookieWatcher(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._check_loop.start()

    def cog_unload(self) -> None:
        self._check_loop.cancel()

    # ── startup check ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        status  = cookie_monitor.get_status()
        summary = cookie_monitor.status_summary(status)
        log.info("Cookie status on startup: %s", summary)

        if status["expired"] or status["near_expiry"]:
            await self._send_alert(status)

    # ── 24 h periodic check ───────────────────────────────────────────────────

    @tasks.loop(hours=24)
    async def _check_loop(self) -> None:
        status  = cookie_monitor.get_status()
        summary = cookie_monitor.status_summary(status)
        log.info("Cookie status (24h check): %s", summary)

        if status["expired"] or status["near_expiry"]:
            await self._send_alert(status)

    @_check_loop.before_loop
    async def _before_check_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ── alert sender ──────────────────────────────────────────────────────────

    async def _send_alert(self, status: cookie_monitor.CookieStatus) -> None:
        """Broadcast an alert embed to every music channel."""
        embed = _build_alert_embed(status)

        if not _MUSIC_CHANNEL_IDS:
            log.warning(
                "Cookie alert triggered but MUSIC_CHANNEL_IDS is not set. "
                "Add channel IDs to .env to receive Discord alerts."
            )
            return

        for channel_id in _MUSIC_CHANNEL_IDS:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                log.warning("Cookie alert: channel %s not found.", channel_id)
                continue
            try:
                await channel.send(embed=embed)  # type: ignore[union-attr]
            except discord.HTTPException as exc:
                log.error("Failed to send cookie alert to %s: %s", channel_id, exc)


# ── embed builder ──────────────────────────────────────────────────────────────

def _build_alert_embed(status: cookie_monitor.CookieStatus) -> discord.Embed:
    if status["expired"]:
        color = 0xED4245   # red
        title = "❌ YouTube 쿠키 만료됨"
        desc  = (
            "YouTube 쿠키가 **만료**됐습니다.\n"
            "bgutil PO 토큰으로 **일반 재생은 가능**하지만 "
            "연령제한·지역제한 영상은 재생 안 될 수 있습니다."
        )
    else:
        color = 0xFEE75C   # yellow
        title = "⚠️ YouTube 쿠키 만료 임박"
        desc  = (
            f"쿠키가 **{status['days']}일 후** 만료됩니다.\n"
            "만료 전에 쿠키를 갱신해 주세요."
        )

    embed = discord.Embed(title=title, description=desc, color=color)

    if status["expires_at"]:
        embed.add_field(
            name="만료일",
            value=status["expires_at"].strftime("%Y-%m-%d %H:%M UTC"),
            inline=True,
        )

    embed.add_field(
        name="갱신 방법",
        value="자세한 방법은 `README.md` → **YouTube 쿠키 갱신** 섹션 참고",
        inline=False,
    )
    embed.set_footer(
        text=f"확인 시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CookieWatcher(bot))
