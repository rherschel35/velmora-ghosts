"""
Shared Velmora ghost engine — Discord client setup and cog loading.

Character identity comes from characters/<GHOST_ID>.yaml. Boot with e.g.
GHOST_ID=mordy so Mordy keeps the same voice, lore, and slash commands as
before the shared-engine extract.
"""

from __future__ import annotations

import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from engine.config import configure_logging, load_character

load_dotenv()

INITIAL_COGS = (
    "engine.cogs.personality",
    "engine.cogs.haunting",
    "engine.cogs.commands",
)


def _parse_guild_ids(env_value: str | None):
    """Comma-separated list of server IDs this ghost is allowed to be in.
    If unset, no restriction is applied (not recommended for a bot with a
    live token floating around)."""
    if not env_value:
        return None
    ids = set()
    for part in env_value.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids or None


def build_bot(ghost_id: str | None = None) -> commands.Bot:
    ghost = load_character(ghost_id)
    log = configure_logging(ghost)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(
        command_prefix="!ghost-unused-",
        intents=intents,
        help_command=None,
    )
    bot.ghost = ghost
    bot.log = log
    bot.allowed_guild_ids = _parse_guild_ids(os.getenv("ALLOWED_GUILD_IDS"))
    return bot


async def _leave_if_unauthorized(bot: commands.Bot, guild: discord.Guild) -> bool:
    """If this guild isn't on the allowed list, leave immediately and say
    so in the logs. Returns True if the ghost left."""
    allowed = getattr(bot, "allowed_guild_ids", None)
    if allowed and guild.id not in allowed:
        bot.log.warning(
            "Not authorized for guild %r (id=%s) - leaving immediately.",
            guild.name,
            guild.id,
        )
        await guild.leave()
        return True
    return False


def attach_events(bot: commands.Bot) -> None:
    @bot.event
    async def on_guild_join(guild: discord.Guild):
        """Someone tried to add this ghost to a server it doesn't belong in.
        Leave right away - it should only ever live in Velmora."""
        await _leave_if_unauthorized(bot, guild)

    @bot.event
    async def on_ready():
        bot.log.info(
            "The ghost has arrived (%s). Logged in as %s (id=%s)",
            bot.ghost.id,
            bot.user,
            bot.user.id,
        )

        # Catch any unauthorized guild it's already sitting in too - covers a
        # stale invite link used before ALLOWED_GUILD_IDS was set, or Public
        # Bot getting flipped back on by accident.
        for guild in list(bot.guilds):
            await _leave_if_unauthorized(bot, guild)

        try:
            dev_guild_id = os.getenv("DEV_GUILD_ID")
            if dev_guild_id:
                guild = discord.Object(id=int(dev_guild_id))
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                bot.log.info("Synced %d commands to dev guild %s", len(synced), dev_guild_id)
            else:
                synced = await bot.tree.sync()
                bot.log.info("Synced %d global commands", len(synced))
        except Exception:
            bot.log.exception("Slash command sync failed")

        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=bot.ghost.presence_activity(),
            )
        )


async def run(ghost_id: str | None = None) -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
        )

    bot = build_bot(ghost_id)
    attach_events(bot)

    async with bot:
        for cog in INITIAL_COGS:
            await bot.load_extension(cog)
            bot.log.info("Loaded %s", cog)
        await bot.start(token)


def main(ghost_id: str | None = None) -> None:
    asyncio.run(run(ghost_id))


if __name__ == "__main__":
    main()
