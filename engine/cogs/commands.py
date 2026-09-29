"""
Slash commands for interacting with the ghost directly.

Command names and copy come from characters/<GHOST_ID>.yaml:
- ask command: /seance (Mordy/Finley) or /ask (Maynard)
- attention: /haunt (Mordy) or /watch (Finley/Maynard)
- lore command: /lore (Mordy/Finley) or /experiment (Maynard)
- /mood
- /interact (when interact_partners are configured)
"""

from __future__ import annotations

import logging
import os
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from engine.config import GHOST_TAGS, INTERACT_MARKER

log = logging.getLogger("velmora.commands")

ATTENTION_DURATION_SECONDS = 60 * 60 * 6  # 6 hours


def _embed_color(name: str) -> discord.Color:
    """Accept discord.Color method names or hex strings like 0x8B5FBF / #8B5FBF."""
    raw = (name or "").strip()
    if raw.lower().startswith("0x"):
        try:
            return discord.Color(int(raw, 16))
        except ValueError:
            return discord.Color.dark_grey()
    if raw.startswith("#") and len(raw) in (4, 7):
        try:
            return discord.Color(int(raw[1:], 16))
        except ValueError:
            return discord.Color.dark_grey()
    getter = getattr(discord.Color, raw, None)
    if callable(getter):
        return getter()
    return discord.Color.dark_grey()


class GhostCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ghost = bot.ghost
        self.lore_fragments = list(self.ghost.lore_fragments)

    def _personality(self):
        return self.bot.get_cog("Personality")

    def _partner_display_name(self, partner_id: str) -> str:
        """Resolve a peer ghost's display name, honoring Railway env overrides
        the same way Mordy's slot-1 / slot-2 mapping did."""
        partners = self.ghost.interact_partners
        partner = self.ghost.partner_by_id(partner_id)
        if partner is None:
            return partner_id
        try:
            index = partners.index(partner)
        except ValueError:
            return partner.name
        if index == 0:
            return (
                os.getenv("OTHER_GHOST_1_NAME")
                or os.getenv("OTHER_GHOST_NAME")
                or partner.name
            )
        if index == 1:
            return os.getenv("OTHER_GHOST_2_NAME") or partner.name
        return partner.name

    def _asker_label(self, interaction: discord.Interaction) -> str:
        return self.ghost.speaker_label(interaction.user)

    @app_commands.command(name="seance", description="Ask the ghost of Velmora a question.")
    @app_commands.describe(question="What do you want to ask it?")
    async def seance(self, interaction: discord.Interaction, question: str):
        """Ask mechanic — renamed to /seance or /ask in setup()."""
        personality = self._personality()
        if not personality:
            await interaction.response.send_message(
                self.ghost.cmd("seance", "unavailable"), ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        asker = self._asker_label(interaction)
        plain = str(interaction.user.display_name)
        memory_hint = None
        prior = personality.memories_about(plain, limit=1)
        if prior:
            memory_hint = prior[0]

        ask_cue = self.ghost.commands.get("seance", {}).get("ask_cue")
        if ask_cue:
            cue = str(ask_cue).format(asker=asker, question=question)
        else:
            answer_style = self.ghost.cmd(
                "seance",
                "answer_style",
                "cryptic, but responsive to what was actually asked.",
            )
            cue = (
                f'{asker} has called a seance and asks you directly: "{question}". '
                f"Answer as the ghost - {answer_style}"
            )
        line = await personality.speak(cue, memory_hint=memory_hint, max_tokens=220)

        embed = discord.Embed(
            description=line,
            color=_embed_color(self.ghost.cmd("seance", "embed_color", "dark_purple")),
        )
        author = self.ghost.cmd(
            "seance", "author", "{asker} calls out into the dark..."
        ).format(asker=plain)
        embed.set_author(name=author)
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="haunt",
        description="Set the ghost loose on a specific member for a while.",
    )
    @app_commands.describe(user="Who should the ghost fixate on?")
    async def attention(self, interaction: discord.Interaction, user: discord.Member):
        """Attention mechanic — renamed to /haunt or /watch in setup()."""
        personality = self._personality()
        if not personality:
            await interaction.response.send_message(
                self.ghost.cmd("attention", "unavailable"), ephemeral=True
            )
            return

        if user.bot:
            await interaction.response.send_message(
                self.ghost.cmd("attention", "no_bots"), ephemeral=True
            )
            return

        personality.set_haunt_target(user.id, ATTENTION_DURATION_SECONDS)

        cue = self.ghost.cmd("attention", "cue").format(user=user.display_name)
        line = await personality.speak(cue, max_tokens=150)

        embed = discord.Embed(
            description=line,
            color=_embed_color(self.ghost.cmd("attention", "embed_color", "dark_red")),
        )
        footer = self.ghost.cmd(
            "attention", "footer", "The ghost's attention now turns to {user}."
        ).format(user=user.display_name)
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lore", description="Ask the ghost to reveal a fragment of Velmora's past.")
    async def lore(self, interaction: discord.Interaction):
        """Lore/journal mechanic — renamed to /lore or /experiment in setup()."""
        personality = self._personality()
        if not personality:
            await interaction.response.send_message(
                self.ghost.cmd("lore", "unavailable"), ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        fragment = personality.next_lore_fragment(self.lore_fragments)
        if fragment is None:
            line = await personality.speak(
                self.ghost.cmd("lore", "exhausted_cue"),
                max_tokens=120,
            )
            embed = discord.Embed(description=line, color=discord.Color.dark_grey())
            await interaction.followup.send(embed=embed)
            return

        cue = self.ghost.cmd("lore", "share_cue").format(fragment=fragment)
        # Maynard's journal entries run a bit longer than Mordy/Finley lore.
        max_tokens = 220 if self.ghost.lore_command() == "experiment" else 200
        line = await personality.speak(cue, max_tokens=max_tokens)

        remaining = len(self.lore_fragments) - personality.state.get("lore_index", 0)
        entry_word = "entry" if remaining == 1 else "entries"
        embed = discord.Embed(
            title=self.ghost.cmd("lore", "title", "A fragment surfaces..."),
            description=line,
            color=_embed_color(self.ghost.cmd("lore", "embed_color", "dark_teal")),
        )
        embed.set_footer(
            text=self.ghost.cmd(
                "lore",
                "footer",
                "{remaining} fragment(s) of Velmora's past remain untold.",
            ).format(remaining=remaining, entry_word=entry_word)
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="mood", description="(admin) Peek at the ghost's current mood.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mood(self, interaction: discord.Interaction):
        personality = self._personality()
        if not personality:
            await interaction.response.send_message("No mood to report.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"{self.ghost.resolved_name()}'s current mood: `{personality.current_mood()}`",
            ephemeral=True,
        )

    @mood.error
    async def mood_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                self.ghost.cmd(
                    "mood",
                    "denied",
                    "You lack the standing to demand that of it.",
                ),
                ephemeral=True,
            )
        else:
            log.exception("Unhandled error in /mood", exc_info=error)

    @app_commands.command(name="interact", description="Call out to another ghost for a brief exchange.")
    @app_commands.describe(who="Which ghost to call out to. Leave blank and one is picked at random.")
    async def interact_command(
        self,
        interaction: discord.Interaction,
        who: app_commands.Choice[str] | None = None,
    ):
        personality = self._personality()
        haunting = self.bot.get_cog("Haunting")
        if not personality or not haunting:
            await interaction.response.send_message("No answer comes.", ephemeral=True)
            return

        partners = self.ghost.interact_partners
        if not partners:
            await interaction.response.send_message("No answer comes.", ephemeral=True)
            return

        choice = who.value if who else random.choice([p.id for p in partners])
        partner = self.ghost.partner_by_id(choice)
        if partner is None:
            await interaction.response.send_message("No answer comes.", ephemeral=True)
            return

        target_name = self._partner_display_name(choice)
        target_tag = GHOST_TAGS[choice]

        channel_id = interaction.channel_id
        # (Re)start the exchange for this channel: this call-out is message 1.
        haunting.exchange_turns[channel_id] = {"total": 1, "last_at": time.time()}

        await interaction.response.defer(thinking=True)

        cue = (
            f"Call out, in character, to {target_name}, another spirit who shares this place with "
            "you - address them directly, in front of everyone, inviting a response, as if starting "
            "a conversation between the two of you."
        )
        line = await personality.speak(cue, max_tokens=150)
        # Sent as a plain channel message rather than an interaction followup:
        # the other ghost's bot reads this over the gateway, and a followup
        # doesn't reliably carry its content to other bots. The trailing tag
        # says who it's aimed at; the marker says it's genuine /interact
        # traffic and not just something to eavesdrop on.
        await interaction.delete_original_response()
        await interaction.channel.send(line + target_tag + INTERACT_MARKER)


def _rename_command(command, name: str, description: str | None = None) -> None:
    command.name = name
    if description:
        command.description = description


async def setup(bot: commands.Bot):
    cog = GhostCommands(bot)
    ghost = bot.ghost

    # Wire slash command names from character YAML so Mordy/Finley keep
    # /seance+/haunt+/lore while Maynard gets /ask+/watch+/experiment.
    _rename_command(
        cog.seance,
        ghost.seance_command(),
        ghost.cmd("seance", "description", cog.seance.description),
    )
    if "question" in cog.seance._params:
        cog.seance._params["question"].description = ghost.cmd(
            "seance",
            "question_describe",
            "What do you want to ask it?",
        )

    _rename_command(
        cog.attention,
        ghost.attention_command(),
        ghost.cmd("attention", "description", cog.attention.description),
    )
    if "user" in cog.attention._params:
        user_param = cog.attention._params["user"]
        user_param.description = ghost.cmd(
            "attention",
            "user_describe",
            "Who should the ghost fixate on?",
        )
        # Maynard's old bot exposed the option as `member` rather than `user`.
        rename_to = ghost.commands.get("attention", {}).get("user_param")
        if rename_to and rename_to != "user":
            user_param._rename = rename_to

    _rename_command(
        cog.lore,
        ghost.lore_command(),
        ghost.cmd("lore", "description", cog.lore.description),
    )

    partners = ghost.interact_partners
    if partners:
        choices = [app_commands.Choice(name=p.name, value=p.id) for p in partners]
        cog.interact_command._params["who"].choices = choices
    else:
        # Characters without peer ghosts don't expose /interact (e.g. Maynard).
        cog.__cog_app_commands__ = [
            cmd for cmd in cog.__cog_app_commands__ if cmd.name != "interact"
        ]
    await bot.add_cog(cog)
