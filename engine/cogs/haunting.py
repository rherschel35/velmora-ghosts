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
from engine.cogs.personality import is_silence

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
        # After an unasked line, stay quiet in that channel this long (Maynard).
        self._chime_cooldown = int(
            self.ghost.haunt("chime_cooldown_seconds", 0) or 0
        )
        self._last_chime: dict[int, float] = {}
        # Sebastian: at most one unasked housemate chime per channel per cooldown.
        self._house_chime_cooldown = int(
            self.ghost.haunt("house_chime_cooldown_seconds", 0) or 0
        )
        self._last_house_chime: dict[int, float] = {}
        tournament_pat = self.ghost.haunt("tournament_pattern")
        self._tournament_pattern = (
            re.compile(tournament_pat, re.IGNORECASE) if tournament_pat else None
        )

    def _chime_ready(self, channel_id: int) -> bool:
        if self._chime_cooldown <= 0:
            return True
        return time.time() - self._last_chime.get(channel_id, 0) >= self._chime_cooldown

    def _house_chime_ready(self, channel_id: int) -> bool:
        if self._house_chime_cooldown <= 0:
            return True
        return (
            time.time() - self._last_house_chime.get(channel_id, 0)
            >= self._house_chime_cooldown
        )

    def is_tournament_talk(self, content: str) -> bool:
        if not self._tournament_pattern:
            return False
        return bool(self._tournament_pattern.search(content or ""))

    def match_keyword(self, content: str, rng=random, housemate: bool = False):
        """Return (keyword, cue_or_None).

        cue is None when a keyword matched but its chance roll failed (Maynard's
        what-if/prank are ~25%). Callers must not fall through to ambient asides
        in that case. Plain string triggers (Mordy/Finley) always have chance 1.0.

        Sebastian: name is the only trigger; tournament talk or housemate status
        swaps the cue (and the keyword label used for token budgets).
        """
        lowered = (content or "").lower().replace("'", "").replace("\u2019", "")
        name_keyword = (self.ghost.haunt("name_keyword") or "").lower()

        # Sebastian-style name-only matching with tournament / house variants.
        if name_keyword and self.ghost.haunt("tournament_cue"):
            pattern = self._keyword_patterns.get(name_keyword)
            if not pattern or not pattern.search(lowered):
                return None, None
            if self.is_tournament_talk(content):
                return "tournament", str(self.ghost.haunt("tournament_cue"))
            if housemate and self.ghost.haunt("house_name_cue"):
                return "house", str(self.ghost.haunt("house_name_cue"))
            trigger = self.ghost.keyword_triggers.get(name_keyword)
            return name_keyword, (trigger.cue if trigger else None)

        for keyword, trigger in self.ghost.keyword_triggers.items():
            if self._keyword_patterns[keyword].search(lowered):
                if trigger.chance >= 1.0 or rng.random() < trigger.chance:
                    return keyword, trigger.cue
                return keyword, None
        return None, None

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
                    "content": f"{self.ghost.speaker_label(msg.author)}: {text}",
                })

        housemate = self.ghost.is_housemate(message.author)
        if housemate and self.ghost.haunt("house_role_name"):
            direction = (
                "One of your own Thornmere students is talking to you directly - the last message above. "
                "With your own house you're at ease: warm, glad they came to you, and happy to talk. Answer "
                "in two or three sentences, carry on naturally from anything you said before, and feel free "
                "to ask them something back. Everything above is a real exchange you were part of - never say "
                "you don't remember it or break character. If it's about the tournament, your passion takes over."
            )
        elif replying_to_me:
            if self.ghost.allow_silence:
                direction = (
                    "Someone has just replied directly to something you said, and their reply is the last "
                    "message above. Answer them, in character, carrying on naturally from your own last "
                    "message. Everything above is a real exchange you were part of - never say you don't "
                    "remember it, never question whether you said it, and never apologise or break character "
                    "to explain yourself. Stay shy - a sentence or two at most - unless the talk is about the "
                    "tournament, where your passion takes over."
                )
            else:
                direction = (
                    "Someone has just replied directly to something you said, and their reply is the last "
                    "message above. Answer them, in character, carrying on naturally from your own last "
                    "message. Everything above is a real exchange you were part of - never say you don't "
                    "remember it, never question whether you said it, and never apologise or break character "
                    "to explain yourself. Keep it to a couple of sentences."
                )
        else:
            if self.ghost.allow_silence:
                direction = (
                    "Someone has just spoken to you directly. Answer them in character - shy, brief, "
                    "a little flustered to be noticed, but you DO answer. If it's about the tournament, "
                    "the shyness falls away and your passion shows."
                )
            else:
                direction = (
                    "Someone has just spoken to you directly, by name. Answer them in character, briefly."
                )

        speaker = self.ghost.speaker_label(message.author)
        if housemate and self.ghost.haunt("house_role_name"):
            speaker = f"{message.author.display_name} (a Thornmere student - your house)"
        tokens = 260 if (housemate or self.ghost.allow_silence) else 200
        async with message.channel.typing():
            line = await personality.speak(
                f"{speaker}: {asked}",
                max_tokens=tokens,
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
        plain_name = str(message.author.display_name)
        author_name = self.ghost.speaker_label(message.author)
        housemate = self.ghost.is_housemate(message.author)

        # Remember most messages with enough substance, so the ghost has
        # material to resurface later. Skip very short/low-content ones.
        if len(content.strip()) >= 12:
            if personality.remember(plain_name, content, message.channel.id):
                # Enough new talk has piled up - condense it into the ghost's
                # running notes in the background.
                asyncio.create_task(self._write_notes_safely(personality))

        # A direct reply to something this ghost said - or an @mention - always
        # gets a real answer, so follow-up questions actually work.
        if await self._maybe_answer_direct_address(message, personality):
            return

        haunted = personality.is_haunted(message.author.id)
        keyword, matched_cue = self.match_keyword(content, housemate=housemate)
        name_keyword = (self.ghost.haunt("name_keyword") or "").lower()
        attention_chance = float(self.ghost.haunt("attention_chance", 0.35))
        random_chance = float(self.ghost.haunt("random_chance", 0.01))
        pun_chance = float(self.ghost.haunt("pun_chance", 0) or 0)
        house_chime_chance = float(self.ghost.haunt("house_chime_chance", 0) or 0)

        cue = None
        unasked = True
        allow_silence = False
        tokens = 180

        if keyword and matched_cue and name_keyword and keyword in (
            name_keyword, "tournament", "house"
        ):
            # Called by name (Maynard always / Sebastian shy|tournament|house).
            unasked = False
            if self.ghost.haunt("tournament_cue"):
                speaker = (
                    f"{author_name}, a Thornmere student,"
                    if housemate else "They"
                )
                cue = f'{matched_cue} {speaker} said: "{content}"'
                name_tokens = self.ghost.haunt("name_tokens") or {}
                tokens = int(name_tokens.get(keyword, 120))
            else:
                cue = f'{matched_cue} {author_name} said: "{content}"'
        elif self._chime_cooldown > 0 and not self._chime_ready(message.channel.id):
            return
        elif matched_cue:
            # Mordy/Finley keep the original "They said" cue wording; Maynard
            # (with a name_keyword configured) uses the speaker label.
            if name_keyword and not self.ghost.haunt("tournament_cue"):
                cue = f'{matched_cue} {author_name} said: "{content}"'
            elif not name_keyword:
                cue = f'{matched_cue} They said: "{content}"'
            else:
                cue = f'{matched_cue} {author_name} said: "{content}"'
        elif keyword:
            # Keyword heard but chance failed — stay quiet; don't ambient-aside.
            return
        elif (
            housemate
            and house_chime_chance > 0
            and len(content.strip()) >= 12
            and self._house_chime_ready(message.channel.id)
            and random.random() < house_chime_chance
        ):
            self._last_house_chime[message.channel.id] = time.time()
            house_cue = str(self.ghost.haunt("house_chime_cue") or "").format(
                content=content
            )
            cue = f"{author_name}: {house_cue}"
            allow_silence = True
            tokens = 120
        elif haunted and self.ghost.has_attention_command() and random.random() < attention_chance:
            cue = self.ghost.cmd(
                "attention",
                "passive_cue",
                (
                    f"You are currently fixated on haunting {author_name} specifically. "
                    f'They just said: "{content}". Slip into their conversation uninvited, '
                    "referencing what they said, as if you'd been waiting for them to speak."
                ),
            ).format(user=author_name, content=content)
        elif pun_chance > 0 and len(content.strip()) >= 12 and random.random() < pun_chance:
            pun_cue = self.ghost.haunt("pun_cue")
            cue = str(pun_cue).format(content=content) if pun_cue else (
                f'Someone said: "{content}". Only if a good pun fits, slip it in. '
                "Otherwise reply with exactly SKIP."
            )
            allow_silence = True
            tokens = 120
        elif random.random() < random_chance and not self.ghost.haunt("tournament_cue"):
            # Mordy/Finley/Maynard ambient asides — Sebastian stays quieter
            # (pun_chance path above is his only unasked channel).
            random_cue = self.ghost.commands.get("attention", {}).get("random_cue")
            if random_cue:
                cue = str(random_cue).format(content=content, user=author_name)
            else:
                cue = (
                    f'Someone said: "{content}". React to it in passing, briefly, as an aside.'
                )

        if not cue:
            return

        if unasked and self._chime_cooldown > 0:
            self._last_chime[message.channel.id] = time.time()

        if keyword and not allow_silence:
            async with message.channel.typing():
                line = await personality.speak(cue, max_tokens=tokens)
        else:
            memory_hint = None
            if not allow_silence and random.random() < 0.3:
                memory_hint = personality.random_memory(exclude_author=plain_name)
            if allow_silence:
                line = await personality.speak(
                    cue, max_tokens=tokens, allow_silence=True
                )
            else:
                async with message.channel.typing():
                    line = await personality.speak(cue, memory_hint=memory_hint)

        if is_silence(line):
            return

        try:
            await message.channel.send(line)
        except discord.HTTPException:
            log.exception("Failed to send haunting reaction in %s", message.channel.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Haunting(bot))
