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
}

# Trailing zero-width space marking genuine /interact traffic.
INTERACT_MARKER = "\u200b"


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
class GhostConfig:
    id: str
    name: str
    self_lore_key: str
    presence: str
    logger: str
    moods: list[str]
    relevant_history_pairs: set[str]
    fallback_lines: list[str]
    keyword_triggers: dict[str, str]
    system_prompt: str
    lore_fragments: list[str]
    prompt_names: dict[str, str] = field(default_factory=dict)
    interact_partners: list[InteractPartner] = field(default_factory=list)

    # Resolved paths / dirs
    lore_dir: Path = LORE_DIR
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("STATE_DIR", str(LORE_DIR.parent))))

    @property
    def self_tag(self) -> str:
        try:
            return GHOST_TAGS[self.id]
        except KeyError as exc:
            raise KeyError(f"No GHOST_TAGS entry for ghost id {self.id!r}") from exc

    @property
    def history_path(self) -> Path:
        return self.lore_dir / "shared_history.json"

    @property
    def velmora_lore_path(self) -> Path:
        return self.lore_dir / "velmora_lore.json"

    @property
    def store_path(self) -> Path:
        return self.state_dir / "memory_store.json"

    def other_ghost_name(self) -> str:
        return os.getenv("OTHER_GHOST_NAME", self.prompt_names.get("other_ghost", "the other ghost"))

    def sebastian_name(self) -> str:
        return os.getenv("SEBASTIAN_NAME", self.prompt_names.get("sebastian", "Sebastian Thornmere"))

    def maynard_name(self) -> str:
        return os.getenv("MAYNARD_NAME", self.prompt_names.get("maynard", "Maynard Moonveil"))

    def resolved_name(self) -> str:
        return os.getenv("GHOST_NAME", self.name)

    def presence_activity(self) -> str:
        return self.presence.format(ghost_name=self.resolved_name())

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

    cfg = GhostConfig(
        id=ghost_id,
        name=_require(raw, "name"),
        self_lore_key=str(raw.get("self_lore_key") or ghost_id),
        presence=str(raw.get("presence") or "Velmora as {ghost_name}"),
        logger=str(raw.get("logger") or "velmora"),
        moods=list(_require(raw, "moods")),
        relevant_history_pairs=set(raw.get("relevant_history_pairs") or []),
        fallback_lines=list(_require(raw, "fallback_lines")),
        keyword_triggers=dict(_require(raw, "keyword_triggers")),
        system_prompt=str(_require(raw, "system_prompt")),
        lore_fragments=list(raw.get("lore_fragments") or []),
        prompt_names=dict(raw.get("prompt_names") or {}),
        interact_partners=partners,
        state_dir=state_dir,
    )
    return cfg


def configure_logging(cfg: GhostConfig) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger(cfg.logger)
