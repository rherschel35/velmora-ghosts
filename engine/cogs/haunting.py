"""
Passive haunting behavior: the ghost noticing things without being asked.

- No unprompted chatter: the ghost only ever speaks in response to a real
  message from someone in the channel.
- Keyword-triggered reactions to certain words in ordinary messages.
- Remembering things members say, occasionally resurfacing an old memory,
  and periodically condensing recent activity into running notes about
  what is actually going on in the server.
- Extra attention on anyone currently under a /haunt effect.
- A capped, on-demand exchange with peer ghost bots, triggered by /interact
  - see engine.cogs.commands for the command itself.

Character-specific keywords, self tag, and peer ghost slots come from
bot.ghost (characters/<GHOST_ID>.yaml).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time

import discord
from discord.ext import commands

from engine.config import INTERACT_MARKER

log = logging.getLogger("velmora.haunting")


def _parse_channel_ids(env_value: str | None):
    if not env_value:
        return None
    ids = set()
    for part in env_value.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids or None


EXCHANGE_MAX_MESSAGES = 3
EXCHANGE_TIMEOUT_SECONDS = 300


class Haunting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ghost = bot.ghost
        self.allowed_channel_ids = _parse_channel_ids(os.getenv("HAUNT_CHANNEL_IDS"))
        # channel_id -> {"total": int, "last_at": float} - this bot's own
        # turn budget for an active /interact exchange in that channel.
        self.exchange_turns = {}
        self.other_ghosts = self.ghost.build_other_ghosts()
        self.self_tag = self.ghost.self_tag
        self._keyword_patterns = {
            kw: re.compile(r"\b" + re.escape(kw.replace("'", "")) + r"\b")
            for kw in self.ghost.keyword_triggers
        }

    def match_keyword(self, content: str):
        lowered = (content or "").lower().replace("'", "").replace("\u2019", "")
        for keyword, cue in self.ghost.keyword_triggers.items():
            if self._keyword_patterns[keyword].search(lowered):
                return cue
        return None

    async def _maybe_reply_to_other_ghost(self, message: discord.Message):
        """Handle a message from another ghost bot during an /interact
        exchange. Only answers when the message is addressed to THIS ghost -
        with three bots sharing a channel, an untargeted call-out would pull
        everyone in at once."""
        content = message.content or ""
        if not content.endswith(INTERACT_MARKER):
            # Not deliberate /interact traffic - just something the other
            # ghost said on its own. Not ours to answer.
            return
        body = content[: -len(INTERACT_MARKER)]
        if not body.endswith(self.self_tag):
            # Aimed at one of the other ghosts. Stay out of it.
            return
        body = body[: -len(self.self_tag)]

        other = self.other_ghosts.get(message.author.id)
        if not other:
            return

        channel_id = message.channel.id
        now = time.time()
        state = self.exchange_turns.get(channel_id)
        if state and now - state["last_at"] > EXCHANGE_TIMEOUT_SECONDS:
            state = None
        total_so_far = state["total"] if state else 0
        total_after_hearing = total_so_far + 1
        if total_after_hearing >= EXCHANGE_MAX_MESSAGES:
            return

        personality = self.bot.get_cog("Personality")
        if not personality:
            return

        cue = (
            f'{other["name"]}, another spirit who shares this place with you, just said: '
            f'"{body}". Reply directly to them, in character, as part of a brief public '
            "back-and-forth between the two of you. Keep it short and let your personalities "
            "play off each other."
        )

        async with message.channel.typing():
            line = await personality.speak(cue, max_tokens=150)

        try:
            # Address the reply back to whoever spoke, so the exchange stays
            # between the two of you.
            await message.channel.send(line + other["tag"] + INTERACT_MARKER)
        except discord.HTTPException:
            log.exception("Failed to send cross-ghost reply in %s", channel_id)
            return

        self.exchange_turns[channel_id] = {"total": total_after_hearing + 1, "last_at": time.time()}

    async def _write_notes_safely(self, personality):
        """Condense recent activity into the ghost's running notes. Runs as a
        background task so it never delays a reply, and swallows its own
        errors - memory is a nicety, not worth breaking a response over."""
        try:
            await personality.update_notes()
        except Exception:
            log.exception("Failed to update server notes")

    async def _resolve_reply_chain(self, message: discord.Message, limit: int = 3):
        """Walk up a Discord reply chain from `message`, nearest first, so a
        follow-up question can be answered with the context of what was
        actually said rather than in a vacuum."""
        chain = []
        current = message
        for _ in range(limit):
            ref = getattr(current, "reference", None)
            if not ref:
                break
            original = ref.resolved if isinstance(ref.resolved, discord.Message) else None
            if original is None and ref.message_id:
                try:
                    original = await current.channel.fetch_message(ref.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    break
            if original is None:
                break
            chain.append(original)
            current = original
        return chain

    async def _maybe_answer_direct_address(self, message: discord.Message, personality) -> bool:
        """Someone replied to something this ghost said, or mentioned it by
        name. Direct address always earns a real answer - no dice roll, no
        keyword required - and the ghost answers in the reply thread so the
        exchange stays readable. Returns True if it answered."""
        me = self.bot.user
        if me is None:
            return False

        chain = await self._resolve_reply_chain(message)
        replying_to_me = bool(chain) and chain[0].author.id == me.id
        mentioned = any(u.id == me.id for u in message.mentions)

        if not (replying_to_me or mentioned):
            return False

        author_name = str(message.author.display_name)
        # Strip raw mention markup so the ghost doesn't read "<@12345>" as words.
        asked = re.sub(r"<@!?&?\d+>", "", message.content or "").strip()
        if not asked:
            return False

        # Hand the exchange over as REAL conversation turns rather than
        # quoting it inside the prompt. Describing a ghost's own past message
        # back to it ("you said X") invites it to doubt whether it really did;
        # passing it as its own assistant turn does not.
        history = []
        for msg in reversed(chain):
            text = (msg.content or "").strip()
            if not text:
                continue
            if msg.author.id == me.id:
                history.append({"role": "assistant", "content": text})
            else:
                history.append({
                    "role": "user",
                    "content": f"{msg.author.display_name}: {text}",
                })

        if replying_to_me:
            direction = (
                "Someone has just replied directly to something you said, and their reply is the last "
                "message above. Answer them, in character, carrying on naturally from your own last "
                "message. Everything above is a real exchange you were part of - never say you don't "
                "remember it, never question whether you said it, and never apologise or break character "
                "to explain yourself. Keep it to a couple of sentences."
            )
        else:
            direction = (
                "Someone has just spoken to you directly, by name. Answer them in character, briefly."
            )

        async with message.channel.typing():
            line = await personality.speak(
                f"{author_name}: {asked}",
                max_tokens=200,
                history=history,
                direction=direction,
            )

        try:
            await message.reply(line, mention_author=False)
        except discord.HTTPException:
            log.exception("Failed to answer direct address in %s", message.channel.id)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            if message.author.id in self.other_ghosts and message.guild:
                await self._maybe_reply_to_other_ghost(message)
            return
        if not message.guild:
            return
        if self.allowed_channel_ids and message.channel.id not in self.allowed_channel_ids:
            return

        personality = self.bot.get_cog("Personality")
        if not personality:
            return

        # Drift the mood as people talk. Shifts wait about two hours.
        personality.maybe_shift_mood()

        content = message.content or ""
        author_name = str(message.author.display_name)

        # Remember most messages with enough substance, so the ghost has
        # material to resurface later. Skip very short/low-content ones.
        if len(content.strip()) >= 12:
            if personality.remember(author_name, content, message.channel.id):
                # Enough new talk has piled up - condense it into the ghost's
                # running notes in the background.
                asyncio.create_task(self._write_notes_safely(personality))

        # A direct reply to something this ghost said - or an @mention - always
        # gets a real answer, so follow-up questions actually work.
        if await self._maybe_answer_direct_address(message, personality):
            return

        haunted = personality.is_haunted(message.author.id)

        matched_cue = self.match_keyword(content)

        should_respond = False
        cue = None

        if matched_cue:
            should_respond = True
            cue = f'{matched_cue} They said: "{content}"'
        elif haunted and random.random() < 0.35:
            should_respond = True
            # Character YAML supplies Mordy's /haunt cue or Finley's /watch cue.
            cue = self.ghost.cmd(
                "attention",
                "passive_cue",
                (
                    f"You are currently fixated on haunting {author_name} specifically. "
                    f'They just said: "{content}". Slip into their conversation uninvited, '
                    "referencing what they said, as if you'd been waiting for them to speak."
                ),
            ).format(user=author_name, content=content)
        elif random.random() < 0.01:
            # rare ambient reaction to an ordinary message
            should_respond = True
            cue = f'Someone said: "{content}". React to it in passing, briefly, as an aside.'

        if not should_respond:
            return

        async with message.channel.typing():
            memory_hint = None
            if random.random() < 0.3:
                memory_hint = personality.random_memory(exclude_author=author_name)
            line = await personality.speak(cue, memory_hint=memory_hint)

        try:
            await message.channel.send(line)
        except discord.HTTPException:
            log.exception("Failed to send haunting reaction in %s", message.channel.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Haunting(bot))
