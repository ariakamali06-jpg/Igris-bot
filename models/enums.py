"""Shared enumerations.

``Slot`` is the loadout vocabulary.  It appears as a database CHECK
constraint, as the layer directory name on disk, and as callback-data in
Telegram, so it is deliberately a ``str``-Enum to serialise cleanly both ways.
"""

from __future__ import annotations

from enum import StrEnum


class Slot(StrEnum):
    """Equipment slots, ordered bottom-to-top of the compositing stack."""

    HEAD = "head"
    BODY = "body"
    LEGS = "legs"
    WEAPON = "weapon"
    ACCESSORY = "accessory"
    AURA = "aura"

    @property
    def label(self) -> str:
        return _SLOT_LABELS[self]

    @property
    def emoji(self) -> str:
        return _SLOT_EMOJI[self]


# Render order: index 0 is drawn first (furthest back).
SLOT_RENDER_ORDER: tuple[Slot, ...] = (
    Slot.LEGS,
    Slot.BODY,
    Slot.HEAD,
    Slot.ACCESSORY,
    Slot.WEAPON,
    Slot.AURA,
)

# Full layer stack, index 0 = bottom of the image.
# 0 background, 1 body, 2 legs, 3 body-top, 4 head, 5 face, 6 weapon, 7 aura.
LAYER_ORDER: tuple[str, ...] = (
    "backgrounds",
    "body",
    "legs",
    "tops",
    "hair",
    "accessories",
    "weapons",
    "auras",
)

# Which layer folder a given slot writes into. Slots are cosmetic-only for
# some layers (e.g. a hat lands in ``hair`` if no dedicated hat folder exists),
# but every slot maps to exactly one folder.
SLOT_LAYER_FOLDER: dict[Slot, str] = {
    Slot.LEGS: "legs",
    Slot.BODY: "tops",
    Slot.HEAD: "hair",
    Slot.ACCESSORY: "accessories",
    Slot.WEAPON: "weapons",
    Slot.AURA: "auras",
}

# Slot that occupies layer index 0..7 in the compositor.
LAYER_SLOT: dict[int, Slot | None] = {
    0: None,            # background (not equippable, chosen per render)
    1: None,            # base character body (always present)
    2: Slot.LEGS,
    3: Slot.BODY,
    4: Slot.HEAD,
    5: Slot.ACCESSORY,
    6: Slot.WEAPON,
    7: Slot.AURA,
}


class Rarity(StrEnum):
    COMMON = "common"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"

    @property
    def label(self) -> str:
        return _RARITY_LABELS[self]

    @property
    def color(self) -> tuple[int, int, int]:
        """Hex-free RGB accent used by both the compositor HUD and keyboards."""
        return _RARITY_COLORS[self]


class ActivityKind(StrEnum):
    """Ledger reason codes — every balance mutation carries one."""

    DAILY = "daily"
    WORK = "work"
    HEIST = "heist"
    HEIST_ARREST = "heist_arrest"
    CASINO = "casino"
    DUEL_STAKE = "duel_stake"
    DUEL_PAYOUT = "duel_payout"
    DUEL_REFUND = "duel_refund"
    DUEL_RAKE = "duel_rake"
    RAID = "raid"
    SHOP_BUY = "shop_buy"
    ADMIN = "admin"
    SYSTEM = "system"


_SLOT_LABELS: dict[Slot, str] = {
    Slot.HEAD: "Head",
    Slot.BODY: "Body",
    Slot.LEGS: "Legs",
    Slot.WEAPON: "Weapon",
    Slot.ACCESSORY: "Accessory",
    Slot.AURA: "Aura",
}

_SLOT_EMOJI: dict[Slot, str] = {
    Slot.HEAD: "🧢",
    Slot.BODY: "🧥",
    Slot.LEGS: "👖",
    Slot.WEAPON: "🔪",
    Slot.ACCESSORY: "🎭",
    Slot.AURA: "✨",
}

_RARITY_LABELS: dict[Rarity, str] = {
    Rarity.COMMON: "Common",
    Rarity.RARE: "Rare",
    Rarity.EPIC: "Epic",
    Rarity.LEGENDARY: "Legendary",
}

_RARITY_COLORS: dict[Rarity, tuple[int, int, int]] = {
    Rarity.COMMON: (158, 158, 158),
    Rarity.RARE: (64, 196, 255),
    Rarity.EPIC: (179, 136, 255),
    Rarity.LEGENDARY: (255, 176, 32),
}
