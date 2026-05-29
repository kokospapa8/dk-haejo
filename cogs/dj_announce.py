"""
DJ Announce — sends a one-liner DJ comment to the music channel each time a new song starts.

Repeat-aware: tracks all announced URLs per guild session.
If a song has already been announced (e.g. queue/single repeat), it is skipped.
The seen-set is cleared when the queue ends so a fresh session re-announces.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import anthropic
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

_DJ_SYSTEM = (
    "당신은 유쾌하고 재치 있는 디스코드 뮤직봇의 DJ입니다. "
    "다음 곡이 시작될 때 한 줄 멘트를 한국어로 작성합니다. "
    "규칙: ① 반드시 한 문장, 50자 이내 ② 요청자 정보를 자연스럽게 녹여도 좋음 "
    "③ 매번 다른 스타일(설레는 소개, 추천 이유, 짧은 감상, 유머 등)로 변주 "
    "④ 이모지 1~2개 포함 ⑤ 곡 제목은 멘트에 넣지 말 것 — 별도로 표시됨 "
    "⑥ 큰따옴표나 앞뒤 설명 없이 멘트만 출력."
)


class DJAnnounce(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._anthropic = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        # guild_id → set of webpage_urls already announced in this session
        self._seen: dict[int, set[str]] = {}

    @commands.Cog.listener()
    async def on_music_state_change(self, guild: discord.Guild) -> None:
        music = self.bot.cogs.get("Music")
        if not music:
            return

        song = music.get_current_song(guild)  # type: ignore[union-attr]

        # Queue ended — reset seen set so next session announces fresh
        if not song:
            self._seen.pop(guild.id, None)
            return

        seen = self._seen.setdefault(guild.id, set())

        # Already announced this song in the current session (repeat)
        if song.webpage_url in seen:
            return
        seen.add(song.webpage_url)

        ch: Optional[discord.TextChannel] = music._text_channels.get(guild.id)  # type: ignore[union-attr]
        if not ch:
            return

        comment = await self._generate(song.title, song.requested_by)
        if comment:
            await ch.send(f"🎵 **{song.title}**\n{comment}")

    async def _generate(self, title: str, requested_by: str) -> Optional[str]:
        kst = datetime.now(timezone(timedelta(hours=9)))
        now_str = kst.strftime("%Y년 %m월 %d일 %H시 %M분")
        prompt = f"현재 날짜·시간: {now_str}\n곡: {title} / 요청자: {requested_by}"
        try:
            resp = await self._anthropic.messages.create(
                model=self._model,
                max_tokens=100,
                system=_DJ_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip() if resp.content else ""
            return text or None
        except Exception:
            log.exception("dj_announce: failed to generate comment for %r", title)
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DJAnnounce(bot))
