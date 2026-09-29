"""
The ghost's voice and memory.

Holds:
- Persisted state (mood, remembered quotes, haunt targets, lore progress)
  in STATE_DIR/memory_store.json.
- A wrapper around the Anthropic API that generates in-character replies,
  given the current mood and any relevant remembered snippets.

Character-specific voice, moods, history pairings, and the system prompt
come from characters/<GHOST_ID>.yaml (attached as bot.ghost).

Other cogs call into this one (via bot.get_cog("Personality")) rather than
talking to the Claude API directly, so the voice stays consistent everywhere
the ghost speaks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

from anthropic import AsyncAnthropic
from discord.ext import commands

from engine.cogs.diary import DiaryMixin
from engine.config import GhostConfig

log = logging.getLogger("velmora.personality")

# How the running "what's been happening" notes behave.
NOTES_EVERY_N_MESSAGES = 25   # condense after this many new remembered messages
NOTES_SOURCE_MESSAGES = 30    # how much recent talk to condense from
NOTES_INJECTED = 8            # how many notes the ghost carries into a reply
MAX_NOTES = 15                # total notes kept before the oldest fall away
RECENT_CONTEXT_MESSAGES = 10  # raw recent messages carried into every reply

MODEL = os.getenv("VELMORA_MODEL", "claude-haiku-4-5-20251001")

# Per-person chat budget: after this many ghost replies in the window,
# the ghost fades for a few minutes so one conversation can't rack up cost.
CHAT_REPLY_LIMIT = 10
CHAT_WINDOW_SECONDS = 10 * 60
CHAT_COOLDOWN_SECONDS = 3 * 60

# Characters that may stay quiet (Sebastian) reply with this exact word.
SILENCE = "SKIP"


def is_silence(text: str | None) -> bool:
    """True when the model chose to stay quiet ("SKIP", maybe with stray
    punctuation or quotes around it)."""
    cleaned = (text or "").strip().strip("*_\"'.!").strip().upper()
    return cleaned == SILENCE


def _ago(ts) -> str:
    """How long ago, in plain words: 'just now', '25 min ago', '3 hours ago'."""
    try:
        secs = max(0, time.time() - float(ts))
    except (TypeError, ValueError):
        return "a while ago"
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        h = int(secs // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = int(secs // 86400)
    return f"{d} day{'s' if d != 1 else ''} ago"


def _default_state(moods: list[str]):
    return {
        "mood": random.choice(moods),
        "mood_set_at": time.time(),
        "memories": [],  # list of {"author": str, "content": str, "channel_id": int, "ts": float}
        "haunt_targets": {},  # user_id (str) -> expiry timestamp
        "lore_index": 0,
        "notes": [],  # running observations about what's happening in the server
        "messages_since_notes": 0,
    }


def _load_shared_history(path: Path):
    """The full cross-ghost story bank (all pairings, all ghosts). Each
    ghost filters it down to just the pairings it was actually part of."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Failed to load shared_history.json")
        return []


def _load_velmora_lore(path: Path):
    """The canonical biography of every ghost tied to Velmora. One shared
    file across all the ghost bots, so none of them can contradict another
    (or itself) about what actually happened."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Failed to load velmora_lore.json")
        return {}


def _build_lore_block(lore: dict, self_key: str) -> str:
    """Turn the shared lore file into a system-prompt section: this ghost's
    own life first (including any secret only it knows), then what it knows
    about the others."""
    if not lore:
        return ""

    sections = []

    me = lore.get(self_key)
    if me:
        own = "\n".join(f"- {fact}" for fact in me.get("facts", []))
        sections.append(
            "YOUR OWN HISTORY. This is your actual life and you remember all of it clearly. "
            "Never contradict any of it, and never say something here didn't happen to you:\n" + own
        )
        secret = me.get("secret")
        if secret:
            sections.append("\n".join(f"- {line}" for line in secret))

    others = []
    for key, entry in lore.items():
        if key == self_key:
            continue
        facts = "\n".join(f"  - {fact}" for fact in entry.get("facts", []))
        header = entry.get("name", key)
        house = entry.get("house")
        if house:
            header = f"{header} ({house})"
        others.append(f"{header}:\n{facts}")

    if others:
        sections.append(
            "THE OTHER GHOSTS OF VELMORA AND THEIR HISTORIES. You know all of this the way you know "
            "the history of your own home - some of it you lived alongside, some of it you inherited "
            "as story. Speak to any of it naturally if it comes up, and never contradict it:\n\n"
            + "\n\n".join(others)
        )

    return "\n\n" + "\n\n".join(sections)


class Personality(DiaryMixin, commands.Cog):
    DIARY_MODEL = MODEL

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ghost: GhostConfig = bot.ghost
        self.DIARY_GHOST_NAME = self.ghost.resolved_name()

        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.client = AsyncAnthropic(api_key=api_key) if api_key else None
        if not self.client:
            log.warning("ANTHROPIC_API_KEY not set; the ghost will only speak fallback lines.")

        self.ghost.state_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self.shared_history = _load_shared_history(self.ghost.history_path)
        self.lore_block = _build_lore_block(
            _load_velmora_lore(self.ghost.velmora_lore_path),
            self.ghost.self_lore_key,
        )

        # Per-person reply timestamps + cooldown-until (in-memory; resets on redeploy).
        self._chat_reply_times: dict[int, list[float]] = {}
        self._chat_cooldown_until: dict[int, float] = {}
        # One fade announcement per cooldown stretch so we don't spam the line.
        self._chat_fade_sent: set[int] = set()

        # Long-term memory: seed the diary from what's already remembered (first
        # run only), and write up any finished days still waiting.
        self.diary_backfill_from_memories()
        try:
            asyncio.get_running_loop().create_task(self.write_pending_diary())
        except RuntimeError:
            pass

    # ---------- persistence ----------

    def _load_state(self):
        path = self.ghost.store_path
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                state = _default_state(self.ghost.moods)
                state.update(loaded)
                notes = state.get("notes") or []
                if len(notes) > MAX_NOTES:
                    state["notes"] = notes[-MAX_NOTES:]
                return state
            except (json.JSONDecodeError, OSError):
                log.exception("Failed to load memory store, starting fresh")
        return _default_state(self.ghost.moods)

    def save_state(self):
        try:
            with open(self.ghost.store_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except OSError:
            log.exception("Failed to persist memory store")

    # ---------- mood ----------

    def current_mood(self) -> str:
        return self.state.get("mood", "restless")

    def maybe_shift_mood(self, force: bool = False):
        """Occasionally drift the ghost's mood while people are talking.

        Shifts wait about two hours, so a busy channel keeps one mood for a while.
        """
        age = time.time() - self.state.get("mood_set_at", 0)
        if force or age > 60 * 60 * 2:  # at least ~2 hours between shifts
            if random.random() < 0.5 or force:
                choices = [m for m in self.ghost.moods if m != self.current_mood()]
                if not choices:
                    return
                new_mood = random.choice(choices)
                self.state["mood"] = new_mood
                self.state["mood_set_at"] = time.time()
                self.save_state()
                log.info("Ghost mood shifted to %s", new_mood)

    # ---------- per-person chat cooldown ----------

    def chat_gate(self, user_id: int) -> str | None:
        """Throttle heavy one-on-one chat so a single person can't burn tokens.

        Returns:
          - a fade line the first time the limit is hit (send that once),
          - "" while the cooldown is still running (send nothing),
          - None when it's fine to call Claude as usual.
        """
        now = time.time()
        until = self._chat_cooldown_until.get(user_id, 0.0)
        if now < until:
            if user_id in self._chat_fade_sent:
                return ""
            self._chat_fade_sent.add(user_id)
            return f"*{self.ghost.resolved_name()} flickers and fades…*"

        # Cooldown expired — clear fade marker.
        self._chat_fade_sent.discard(user_id)
        self._chat_cooldown_until.pop(user_id, None)

        times = [
            t for t in self._chat_reply_times.get(user_id, [])
            if now - t < CHAT_WINDOW_SECONDS
        ]
        self._chat_reply_times[user_id] = times
        if len(times) >= CHAT_REPLY_LIMIT:
            self._chat_cooldown_until[user_id] = now + CHAT_COOLDOWN_SECONDS
            self._chat_reply_times[user_id] = []
            self._chat_fade_sent.add(user_id)
            return f"*{self.ghost.resolved_name()} flickers and fades…*"
        return None

    def record_chat_reply(self, user_id: int) -> None:
        """Count a real Claude reply toward this person's chat budget."""
        self._chat_reply_times.setdefault(user_id, []).append(time.time())

    # ---------- memory of things members said ----------

    def remember(self, author: str, content: str, channel_id: int):
        self.state.setdefault("memories", []).append(
            {"author": author, "content": content[:300], "channel_id": channel_id, "ts": time.time()}
        )
        self.diary_record(author, content, time.time())
        # keep it bounded
        self.state["memories"] = self.state["memories"][-200:]
        self.state["messages_since_notes"] = self.state.get("messages_since_notes", 0) + 1
        self.save_state()
        # Caller kicks off note-writing in the background when this goes True.
        return self.state["messages_since_notes"] >= NOTES_EVERY_N_MESSAGES

    def random_memory(self, exclude_author: str | None = None):
        memories = self.state.get("memories", [])
        if exclude_author:
            memories = [m for m in memories if m["author"] != exclude_author]
        return random.choice(memories) if memories else None

    # ---------- shared history with the other ghosts ----------

    def random_shared_story(self):
        """Pick a random past moment this ghost actually took part in, from
        the shared cross-ghost history bank."""
        pairs = self.ghost.relevant_history_pairs
        candidates = [s for s in self.shared_history if s.get("pair") in pairs]
        return random.choice(candidates)["story"] if candidates else None

    def memories_about(self, author: str, limit: int = 3):
        memories = [m for m in self.state.get("memories", []) if m["author"] == author]
        return memories[-limit:]

    # ---------- running notes: what's been happening in the server ----------

    def recent_notes(self, limit: int = NOTES_INJECTED):
        return [n["text"] for n in self.state.get("notes", [])][-limit:]

    def recent_timed_notes(self, limit: int = NOTES_INJECTED):
        return [(n["text"], n.get("ts")) for n in self.state.get("notes", [])][-limit:]

    def recent_conversation(self, limit: int = RECENT_CONTEXT_MESSAGES, max_age_hours: float = 12):
        """The last few remembered messages from roughly the last half-day."""
        cutoff = time.time() - max_age_hours * 3600
        recent = [m for m in self.state.get("memories", []) if m.get("ts", 0) >= cutoff]
        return recent[-limit:]

    async def update_notes(self):
        """Condense the recent things people said into one or two durable
        notes, in this ghost's own voice. Called in the background once
        enough new messages have piled up - never on the reply path, so it
        can't slow a response down."""
        if not self.client:
            return

        memories = self.state.get("memories", [])
        if not memories:
            self.state["messages_since_notes"] = 0
            self.save_state()
            return

        recent = memories[-NOTES_SOURCE_MESSAGES:]
        transcript = "\n".join(f'{m["author"]}: {m["content"]}' for m in recent)
        existing = self.recent_notes()
        already = ""
        if existing:
            already = (
                "\n\nYou have already noted the following, so do NOT repeat them - only record what is "
                "new or what has changed:\n" + "\n".join(f"- {n}" for n in existing)
            )

        ghost_name = self.ghost.resolved_name()
        system = (
            f"You are {ghost_name}, a ghost who has been quietly watching a Discord server called "
            "Velmora. Below is a stretch of what people actually said there. Write ONE or TWO short "
            "notes - a single sentence each - recording what is genuinely going on: what people are "
            "working on, what happened, what changed, who has been around. These are your own private "
            "observations, in your own voice, the way anyone keeps a mental note of their own home. "
            "Record only things that actually happened; never invent. If nothing worth remembering "
            "happened, reply with the single word NOTHING. Output only the notes themselves, one per "
            "line, with no numbering, bullets, or preamble." + already
        )

        try:
            resp = await self.client.messages.create(
                model=MODEL,
                max_tokens=200,
                system=system,
                messages=[{"role": "user", "content": transcript}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception:
            log.exception("Failed to generate server notes")
            return

        self.state["messages_since_notes"] = 0

        if text and text.strip().upper() != "NOTHING":
            existing_texts = {n["text"] for n in self.state.get("notes", [])}
            notes = self.state.setdefault("notes", [])
            for line in text.split("\n"):
                line = line.strip().lstrip("-*0123456789. ").strip()
                if len(line) > 4 and line.upper() != "NOTHING" and line not in existing_texts:
                    notes.append({"text": line, "ts": time.time()})
                    existing_texts.add(line)
            self.state["notes"] = notes[-MAX_NOTES:]
            log.info("Recorded server notes; now holding %d", len(self.state["notes"]))

        self.save_state()

    # ---------- haunt targets ----------

    def set_haunt_target(self, user_id: int, duration_seconds: int):
        self.state.setdefault("haunt_targets", {})[str(user_id)] = time.time() + duration_seconds
        self.save_state()

    def clear_haunt_target(self, user_id: int) -> bool:
        """Remove attention on a member. Returns True if they were being targeted."""
        targets = self.state.setdefault("haunt_targets", {})
        if str(user_id) not in targets:
            return False
        del targets[str(user_id)]
        self.save_state()
        return True

    def is_haunted(self, user_id: int) -> bool:
        expiry = self.state.get("haunt_targets", {}).get(str(user_id))
        if not expiry:
            return False
        if time.time() > expiry:
            del self.state["haunt_targets"][str(user_id)]
            self.save_state()
            return False
        return True

    # ---------- lore ----------

    def next_lore_fragment(self, lore_list):
        idx = self.state.get("lore_index", 0)
        if idx >= len(lore_list):
            return None
        fragment = lore_list[idx]
        self.state["lore_index"] = idx + 1
        self.save_state()
        return fragment

    # ---------- generation ----------

    @staticmethod
    def _normalize_messages(history, user_prompt: str):
        """Build a valid Anthropic message list from real Discord turns.

        The API needs the first turn to be a user turn and roles to
        alternate; a stretch of Discord messages obeys neither rule, so fold
        consecutive same-role turns together and open on a user turn. Passing
        the ghost's own past messages as genuine assistant turns (rather than
        quoting them inside a prompt) is what stops it from second-guessing
        whether it really said them."""
        turns = []
        for turn in (history or []):
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if not content or role not in ("user", "assistant"):
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})

        if turns and turns[0]["role"] == "assistant":
            turns.insert(0, {"role": "user", "content": "(Someone is listening.)"})

        user_prompt = (user_prompt or "").strip()
        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] += "\n\n" + user_prompt
        else:
            turns.append({"role": "user", "content": user_prompt})
        return turns

    async def speak(
        self,
        user_prompt: str,
        memory_hint: dict | None = None,
        max_tokens: int = 180,
        history=None,
        direction: str | None = None,
        allow_silence: bool = False,
    ) -> str:
        """Generate an in-character line from the ghost.

        user_prompt: what the ghost is reacting to (a question, a message
        excerpt, or a cue such as a keyword that just caught its attention).
        memory_hint: an optional remembered {"author", "content"} dict to
        weave in, so the ghost seems to actually recall things.
        history: prior turns of a real exchange, as [{"role", "content"}],
        so a follow-up question is answered with the ghost's own earlier
        messages present as its own turns.
        direction: an extra in-character instruction appended to the system
        prompt for this one call.
        allow_silence: for passing reactions only (Sebastian). If he'd rather
        say nothing, returns SILENCE and the caller sends nothing. When
        someone speaks to him directly this stays False, so he always answers.
        """
        silence_ok = allow_silence and self.ghost.allow_silence
        if not self.client:
            return SILENCE if silence_ok else random.choice(self.ghost.fallback_lines)

        memory_block = ""
        if memory_hint:
            memory_block = (
                f"\n\nYou half-remember this, said by someone here before: "
                f'"{memory_hint["content"]}" - attributed (in your memory, "{memory_hint["author"]}"). '
                "You may allude to it if it fits naturally. Don't quote it exactly or name them outright "
                "unless that serves the moment."
            )

        # Every so often, surface one of the real, specific memories this
        # ghost shares with the others - not just the vague relationship
        # summary above, but an actual moment from the story bank.
        if random.random() < 0.2:
            story = self.random_shared_story()
            if story:
                memory_block += (
                    f'\n\nA specific memory just surfaced, unprompted, the way old memories do: "{story}" '
                    "You may allude to it if it genuinely fits what's happening right now - don't force it "
                    "in, don't narrate the whole thing, and don't quote it verbatim."
                )

        timed_notes = self.recent_timed_notes()
        if timed_notes:
            memory_block += (
                "\n\nWHAT HAS BEEN HAPPENING IN VELMORA LATELY - your own observations, oldest first:\n"
                + "\n".join(f"- ({_ago(ts)}) {text}" for text, ts in timed_notes)
                + "\nThis is real, current context about the people here. Reference it naturally if it "
                "fits what's being said right now - don't recite it, don't list it, and don't force it in."
            )

        # The raw last stretch of conversation, so the ghost knows what's
        # been said in the last few hours - not just what made it into notes.
        recent = self.recent_conversation()
        if recent:
            memory_block += (
                "\n\nTHE MOST RECENT THINGS PEOPLE SAID HERE, oldest first - this is what you've just "
                "been hearing:\n"
                + "\n".join(f'- ({_ago(m["ts"])}) {m["author"]}: {m["content"]}' for m in recent)
                + "\nYou remember all of this. If someone asks what's been going on, or refers back to "
                "something said recently, this is where the answer is. Don't recite it unprompted."
            )

        # Long-term memory: recent diary days, plus any older days that
        # what's being said points back to.
        memory_block += self.diary_block(user_prompt)

        # Split system prompt so the stable personality + lore can be prompt-
        # cached (~1/10 the input price on cache hits). Mood + per-call memory
        # stay in a second uncached block.
        static_system = self.ghost.system_prompt.format(
            **self.ghost.prompt_format_kwargs(),
            mood=self.current_mood(),
            lore_block=self.lore_block,
            memory_block="",
        )
        dynamic_bits = []
        if memory_block.strip():
            dynamic_bits.append(memory_block.strip())
        if direction:
            dynamic_bits.append(direction)
        system: list[dict] = [
            {
                "type": "text",
                "text": static_system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if dynamic_bits:
            system.append({"type": "text", "text": "\n\n".join(dynamic_bits)})

        try:
            resp = await self.client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=self._normalize_messages(history, user_prompt),
            )
            text_parts = [block.text for block in resp.content if block.type == "text"]
            reply = "".join(text_parts).strip()
            if is_silence(reply):
                return SILENCE if silence_ok else random.choice(self.ghost.fallback_lines)
            return reply or random.choice(self.ghost.fallback_lines)
        except Exception:
            log.exception("Claude API call failed")
            return random.choice(self.ghost.fallback_lines)


async def setup(bot: commands.Bot):
    await bot.add_cog(Personality(bot))
