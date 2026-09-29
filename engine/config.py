"""Load a character YAML and resolve env overrides.

Shared lore (velmora_lore.json, shared_history.json) lives under data/lore/.
Mutable runtime state (memory_store.json) lives under STATE_DIR, which must
NOT be the repo data/ folder — a Railway volume mounted over data/ would
hide the shared lore files.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHARACTERS_DIR = ROOT / "characters"
LORE_DIR = ROOT / "data" / "lore"

# Invisible per-ghost address tags used by /interact. Must stay identical
# across every ghost that participates in cross-ghost exchanges.
GHOST_TAGS = {
    "mordy": "\u2060",
    "finley": "\u2061",
    "cassy": "\u2062",
    "maynard": "\u2063",
    "sebastian": "\u2064",
    "vida": "\u2065",
}

# Trailing zero-width space marking genuine /interact traffic.
INTERACT_MARKER = "\u200b"

# Defaults for named peers that may appear in system prompts.
_DEFAULT_PEER_NAMES = {
    "other_ghost": "the other ghost",
    "mordy": "Mordy Velmora",
    "finley": "Finley Veyren",
    "cassy": "Cassy Caldrin",
    "sebastian": "Sebastian Thornmere",
    "maynard": "Maynard Moonveil",
}


@dataclass
class InteractPartner:
    id: str
    name: str

    @property
    def tag(self) -> str:
        try:
            return GHOST_TAGS[self.id]
        except KeyError as exc:
            raise KeyError(f"No GHOST_TAGS entry for interact partner {self.id!r}") from exc


@dataclass
class KeywordTrigger:
    cue: str
    chance: float = 1.0


@dataclass
class GhostConfig:
    id: str
    name: str
    self_lore_key: str
    presence: str
    logger: str
    moods: list[str]
    relevant_history_pairs: set[str]
    fallback_lines: list[str]
    keyword_triggers: dict[str, KeywordTrigger]
    system_prompt: str
    lore_fragments: list[str]
    prompt_names: dict[str, str] = field(default_factory=dict)
    interact_partners: list[InteractPartner] = field(default_factory=list)
    # Slash-command copy + renames (/haunt|/watch, /seance|/ask, /lore|/experiment).
    commands: dict[str, Any] = field(default_factory=dict)
    # Optional passive-haunting knobs (Maynard: cooldown, lower chances, etc.).
    haunting: dict[str, Any] = field(default_factory=dict)
    headmaster_role_id: int | None = None
    # When True, speak(allow_silence=True) may return SKIP / stay quiet.
    allow_silence: bool = False

    # Resolved paths / dirs
    lore_dir: Path = LORE_DIR
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("STATE_DIR", str(LORE_DIR.parent))))

    @property
    def self_tag(self) -> str:
        try:
            return GHOST_TAGS[self.id]
        except KeyError as exc:
            raise KeyError(f"No GHOST_TAGS entry for ghost id {self.id!r}") from exc

    def cmd(self, section: str, key: str, default: str | None = None) -> str:
        """Look up character-specific slash-command copy."""
        block = self.commands.get(section) or {}
        if key in block and block[key] is not None:
            return str(block[key])
        if default is not None:
            return default
        raise KeyError(f"characters/{self.id}.yaml commands.{section}.{key} is required")

    def command_name(self, section: str, default: str) -> str:
        """Slash command name for a logical section (seance/attention/lore)."""
        return self.cmd(section, "command", default)

    def _section_enabled(self, section: str) -> bool:
        block = self.commands.get(section)
        if not block:
            return False
        if block.get("enabled") is False:
            return False
        return True

    def has_attention_command(self) -> bool:
        """False when attention.enabled is false or the section is omitted."""
        return self._section_enabled("attention")

    def has_stop_attention_command(self) -> bool:
        """True when attention.stop_command is set (e.g. /stophaunt, /stopflirt)."""
        if not self.has_attention_command():
            return False
        return bool((self.commands.get("attention") or {}).get("stop_command"))

    def has_seance_command(self) -> bool:
        return self._section_enabled("seance")

    def has_lore_command(self) -> bool:
        return self._section_enabled("lore")

    def has_mood_command(self) -> bool:
        return self._section_enabled("mood")

    def has_pun_command(self) -> bool:
        return self._section_enabled("pun")

    def has_interact_command(self) -> bool:
        if not self.interact_partners:
            return False
        interact = self.commands.get("interact")
        if interact is not None and interact.get("enabled") is False:
            return False
        return True

    def attention_command(self) -> str:
        """Slash command name for the attention mechanic: haunt, watch, tend, flirt."""
        return self.command_name("attention", "haunt")

    def stop_attention_command(self) -> str:
        return str(
            (self.commands.get("attention") or {}).get("stop_command") or "stophaunt"
        )

    def seance_command(self) -> str:
        return self.command_name("seance", "seance")

    def lore_command(self) -> str:
        return self.command_name("lore", "lore")

    def attention_headmasters_only(self) -> bool:
        return bool((self.commands.get("attention") or {}).get("headmasters_only"))

    def house_role_id(self) -> int:
        env = os.getenv("HOUSE_ROLE_ID")
        if env and env.isdigit():
            return int(env)
        raw = self.haunt("house_role_id", 0) or 0
        return int(raw)

    def is_housemate(self, member) -> bool:
        """True if member has this ghost's house role (Sebastian / Thornmere)."""
        role_id = self.house_role_id()
        role_name = str(self.haunt("house_role_name") or "").strip().lower()
        if not role_id and not role_name:
            return False
        for role in getattr(member, "roles", None) or []:
            if role_id and role.id == role_id:
                return True
            if role_name and (role.name or "").strip().lower() == role_name:
                return True
        return False

    def haunt(self, key: str, default: Any = None) -> Any:
        if key in self.haunting:
            return self.haunting[key]
        return default

    @property
    def history_path(self) -> Path:
        return self.lore_dir / "shared_history.json"

    @property
    def velmora_lore_path(self) -> Path:
        return self.lore_dir / "velmora_lore.json"

    @property
    def store_path(self) -> Path:
        return self.state_dir / "memory_store.json"

    def _named(self, key: str) -> str:
        env = os.getenv(f"{key.upper()}_NAME")
        if env:
            return env
        return self.prompt_names.get(key, _DEFAULT_PEER_NAMES.get(key, key))

    def other_ghost_name(self) -> str:
        return os.getenv(
            "OTHER_GHOST_NAME",
            self.prompt_names.get("other_ghost", _DEFAULT_PEER_NAMES["other_ghost"]),
        )

    def other_ghost_1_name(self) -> str:
        """First /interact peer (Cassy: Mordy; Mordy/Finley: slot 1)."""
        default = (
            self.interact_partners[0].name
            if self.interact_partners
            else _DEFAULT_PEER_NAMES["mordy"]
        )
        return (
            os.getenv("OTHER_GHOST_1_NAME")
            or os.getenv("OTHER_GHOST_NAME")
            or default
        )

    def other_ghost_2_name(self) -> str:
        """Second /interact peer (Cassy: Finley)."""
        default = (
            self.interact_partners[1].name
            if len(self.interact_partners) > 1
            else _DEFAULT_PEER_NAMES["finley"]
        )
        return os.getenv("OTHER_GHOST_2_NAME") or default

    def sebastian_name(self) -> str:
        return self._named("sebastian")

    def maynard_name(self) -> str:
        return self._named("maynard")

    def mordy_name(self) -> str:
        return self._named("mordy")

    def finley_name(self) -> str:
        return self._named("finley")

    def cassy_name(self) -> str:
        return self._named("cassy")

    def resolved_name(self) -> str:
        return os.getenv("GHOST_NAME", self.name)

    def prompt_format_kwargs(self) -> dict[str, str]:
        """Name placeholders for system_prompt.format(...).

        Mordy/Finley use {other_ghost_name}/{sebastian_name}/{maynard_name};
        Maynard uses {mordy_name}/{finley_name}/{sebastian_name}/{cassy_name}.
        """
        return {
            "ghost_name": self.resolved_name(),
            "other_ghost_name": self.other_ghost_name(),
            "other_ghost_1_name": self.other_ghost_1_name(),
            "other_ghost_2_name": self.other_ghost_2_name(),
            "sebastian_name": self.sebastian_name(),
            "maynard_name": self.maynard_name(),
            "mordy_name": self.mordy_name(),
            "finley_name": self.finley_name(),
            "cassy_name": self.cassy_name(),
        }

    def presence_activity(self) -> str:
        return self.presence.format(ghost_name=self.resolved_name())

    def is_headmaster(self, member) -> bool:
        env = os.getenv("HEADMASTER_ROLE_ID")
        role_id = int(env) if env and env.isdigit() else self.headmaster_role_id
        for role in getattr(member, "roles", None) or []:
            if role_id and role.id == role_id:
                return True
            if (role.name or "").strip().lower() == "headmasters":
                return True
        return False

    def speaker_label(self, member) -> str:
        name = str(member.display_name)
        return f"{name} (a headmaster)" if self.is_headmaster(member) else name

    def partner_by_id(self, partner_id: str) -> InteractPartner | None:
        for partner in self.interact_partners:
            if partner.id == partner_id:
                return partner
        return None

    def build_other_ghosts(self) -> dict[int, dict[str, str]]:
        """Map Discord user ids of peer ghost bots -> {name, tag}.

        Slot 1 accepts the legacy OTHER_GHOST_ID / OTHER_GHOST_NAME vars so
        existing Railway config keeps working; slot 2 uses OTHER_GHOST_2_*.
        """
        others: dict[int, dict[str, str]] = {}
        if not self.interact_partners:
            return others

        def _int_or_none(value: str | None) -> int | None:
            return int(value) if value and value.isdigit() else None

        slots = [
            (
                _int_or_none(os.getenv("OTHER_GHOST_1_ID") or os.getenv("OTHER_GHOST_ID")),
                os.getenv("OTHER_GHOST_1_NAME") or os.getenv("OTHER_GHOST_NAME"),
                0,
            ),
            (
                _int_or_none(os.getenv("OTHER_GHOST_2_ID")),
                os.getenv("OTHER_GHOST_2_NAME"),
                1,
            ),
        ]
        for ghost_id, name_override, index in slots:
            if ghost_id is None or index >= len(self.interact_partners):
                continue
            partner = self.interact_partners[index]
            others[ghost_id] = {
                "name": name_override or partner.name,
                "tag": partner.tag,
            }
        return others


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise KeyError(f"characters config missing required key: {key}")
    return data[key]


def _normalize_keyword_triggers(raw: dict[str, Any]) -> dict[str, KeywordTrigger]:
    """Accept plain cue strings (Mordy/Finley) or {chance, cue} maps (Maynard)."""
    out: dict[str, KeywordTrigger] = {}
    for keyword, value in (raw or {}).items():
        if isinstance(value, dict):
            cue = str(value.get("cue") or "")
            chance = float(value.get("chance", 1.0))
        else:
            cue = str(value)
            chance = 1.0
        out[str(keyword)] = KeywordTrigger(cue=cue, chance=chance)
    return out


def load_character(ghost_id: str | None = None) -> GhostConfig:
    ghost_id = (ghost_id or os.getenv("GHOST_ID") or "").strip().lower()
    if not ghost_id:
        raise SystemExit(
            "GHOST_ID is not set. Set GHOST_ID to a characters/<id>.yaml stem "
            "(e.g. GHOST_ID=mordy)."
        )

    path = CHARACTERS_DIR / f"{ghost_id}.yaml"
    if not path.exists():
        raise SystemExit(f"No character config at {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if raw.get("id") and str(raw["id"]).lower() != ghost_id:
        raise SystemExit(
            f"Character id mismatch: file is {path.name} but id field is {raw.get('id')!r}"
        )

    partners = [
        InteractPartner(id=p["id"], name=p["name"])
        for p in (raw.get("interact_partners") or [])
    ]

    state_dir = Path(os.getenv("STATE_DIR", str(ROOT / "data")))
    headmaster_role_id = raw.get("headmaster_role_id")
    if headmaster_role_id is not None:
        headmaster_role_id = int(headmaster_role_id)

    cfg = GhostConfig(
        id=ghost_id,
        name=_require(raw, "name"),
        self_lore_key=str(raw.get("self_lore_key") or ghost_id),
        presence=str(raw.get("presence") or "Velmora as {ghost_name}"),
        logger=str(raw.get("logger") or "velmora"),
        moods=list(_require(raw, "moods")),
        relevant_history_pairs=set(raw.get("relevant_history_pairs") or []),
        fallback_lines=list(_require(raw, "fallback_lines")),
        keyword_triggers=_normalize_keyword_triggers(_require(raw, "keyword_triggers")),
        system_prompt=str(_require(raw, "system_prompt")),
        lore_fragments=list(raw.get("lore_fragments") or []),
        prompt_names=dict(raw.get("prompt_names") or {}),
        interact_partners=partners,
        commands=dict(raw.get("commands") or {}),
        haunting=dict(raw.get("haunting") or {}),
        headmaster_role_id=headmaster_role_id,
        allow_silence=bool(raw.get("allow_silence", False)),
        state_dir=state_dir,
    )
    return cfg


def configure_logging(cfg: GhostConfig) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger(cfg.logger)
