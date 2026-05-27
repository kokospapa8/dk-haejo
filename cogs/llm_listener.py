"""
LLM Listener Cog – intercepts Discord messages and routes them through
Claude (Tool Use) to the Music cog.

Triggers on:
  1. Any message in a designated music channel (MUSIC_CHANNEL_IDS env var)
  2. Any message that @mentions the bot (any channel)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import anthropic
import discord
from discord.ext import commands

from cogs.music import Music
from utils.llm_tools import MUSIC_TOOLS

log = logging.getLogger(__name__)

# ── system prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a friendly Discord music bot assistant. "
    "Your job is to interpret the user's natural language message and call "
    "the appropriate music control tool. "
    "Always call exactly one tool per message. "
    "If the user's message does not relate to music control "
    "(e.g., general chat), do NOT call any tool — just reply conversationally "
    "in the same language the user used (Korean or English). "
    "When you call a tool, do NOT add any extra text — the bot will "
    "send the tool result directly to the user."
)

# ── helper: parse MUSIC_CHANNEL_IDS ──────────────────────────────────────────

def _load_channel_ids() -> set[int]:
    raw = os.getenv("MUSIC_CHANNEL_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


class LLMListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.music_channel_ids: set[int] = _load_channel_ids()
        self._anthropic = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        self._model: str = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")

    # ── event listener ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bot messages
        if message.author.bot:
            return
        # Ignore DMs (no voice channels in DMs)
        if not message.guild:
            return

        mentioned = self.bot.user in message.mentions
        in_music_channel = message.channel.id in self.music_channel_ids

        if not (mentioned or in_music_channel):
            return

        # Strip the @mention prefix so Claude sees clean text
        content = message.content
        if mentioned and self.bot.user:
            content = content.replace(f"<@{self.bot.user.id}>", "").strip()
            content = content.replace(f"<@!{self.bot.user.id}>", "").strip()

        if not content:
            await message.reply("무엇을 도와드릴까요? 🎵")
            return

        async with message.channel.typing():
            reply = await self._handle_llm(message, content)

        if reply is None:
            return

        if isinstance(reply, discord.Embed):
            await message.reply(embed=reply)
        else:
            await message.reply(str(reply))


    # ── LLM dispatch ─────────────────────────────────────────────────────────

    async def _handle_llm(
        self, message: discord.Message, user_text: str
    ) -> Optional[str | discord.Embed]:
        """Send *user_text* to Claude with tool definitions; execute the result."""
        try:
            response = self._anthropic.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        # Cache the static system prompt to save API cost
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=MUSIC_TOOLS,  # type: ignore[arg-type]
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.APIError as exc:
            log.exception("Anthropic API error")
            return f"❌ LLM 오류: {exc}"

        # ── find tool_use block ───────────────────────────────────────────────
        tool_block = next(
            (b for b in response.content if b.type == "tool_use"), None
        )

        if tool_block is None:
            # Claude replied conversationally (no tool call)
            text_block = next(
                (b for b in response.content if b.type == "text"), None
            )
            return text_block.text if text_block else None

        return await self._dispatch(message, tool_block.name, tool_block.input)

    # ── tool dispatcher ───────────────────────────────────────────────────────

    async def _dispatch(
        self,
        message: discord.Message,
        tool_name: str,
        inputs: dict,
    ) -> Optional[str | discord.Embed]:
        guild = message.guild
        member = message.author  # type: ignore[assignment]
        music: Optional[Music] = self.bot.cogs.get("Music")  # type: ignore[assignment]

        if music is None:
            log.error("Music cog not loaded")
            return "❌ Music 모듈이 로드되지 않았습니다."

        match tool_name:
            case "play_song":
                return await music.play_song(guild, member, inputs["query"])

            case "pause_playback":
                return await music.pause(guild)

            case "resume_playback":
                return await music.resume(guild)

            case "skip_song":
                return await music.skip(guild)

            case "stop_playback":
                return await music.stop(guild)

            case "view_queue":
                return music.view_queue(guild)

            case "remove_from_queue":
                return await music.remove_from_queue(guild, inputs["index"])

            case "set_repeat":
                return await music.set_repeat(guild, inputs["mode"])

            case "set_volume":
                return await music.set_volume(guild, inputs["level"])

            case "join_voice_channel":
                _, msg = await music.join_channel(member)
                return msg

            case "leave_voice_channel":
                return await music.leave(guild)

            case _:
                log.warning("Unknown tool name: %s", tool_name)
                return f"❌ 알 수 없는 명령어: `{tool_name}`"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LLMListener(bot))
