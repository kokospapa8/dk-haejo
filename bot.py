"""
Entry point – loads cogs, configures intents, and starts the bot.
"""
from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ── logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── bot setup ─────────────────────────────────────────────────────────────────

COGS = [
    "cogs.music",
    "cogs.llm_listener",
    "cogs.cookie_watcher",
    "cogs.version_announce",
]


async def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True   # Required to read message text
    intents.voice_states = True      # Required for voice channel presence

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
        await bot.tree.sync()        # Sync slash commands (if any are added later)

    @bot.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Auto-leave when the bot is the only one left in a voice channel."""
        guild = member.guild
        vc = guild.voice_client
        if not vc:
            return
        # Check if bot is alone
        if len(vc.channel.members) == 1 and guild.me in vc.channel.members:
            from cogs.music import Music
            music: Music | None = bot.cogs.get("Music")  # type: ignore[assignment]
            if music:
                await music.leave(guild)
                log.info("Auto-left voice channel in guild %s (alone)", guild.id)

    async with bot:
        for cog in COGS:
            await bot.load_extension(cog)
            log.info("Loaded cog: %s", cog)

        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_TOKEN environment variable is not set.")
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
