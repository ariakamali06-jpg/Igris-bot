"""The item catalog — single source of truth for gear stats and art keys.

Both the asset generator (``scripts/generate_assets.py``) and the database
seeder import this module, which guarantees ``items.layer_key`` always matches
an actual PNG on disk.  Stats are deliberately small: Drip matters mostly for
Heist luck and shop discounts, ATK/DEF for duels and raids.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.enums import Rarity, Slot


@dataclass(frozen=True, slots=True)
class ItemDef:
    """Static definition of one equippable item."""

    id: str
    name: str
    slot: Slot
    rarity: Rarity
    atk: int = 0
    defense: int = 0
    drip: int = 0
    price_credits: int = 0
    price_soul_shards: int = 0
    description: str = ""
    shop_pool: str = "rotating"  # rotating | permanent | starter

    @property
    def layer_key(self) -> str:
        """PNG basename; matches the file the asset generator writes."""
        return self.id

    @property
    def folder(self) -> str:
        """Layer folder under ``assets/layers/`` for this slot."""
        from models.enums import SLOT_LAYER_FOLDER

        return SLOT_LAYER_FOLDER[self.slot]


# ---------------------------------------------------------------------------
# Non-equippable scene layers (rendered behind/under the character).
# ---------------------------------------------------------------------------
BACKGROUND_KEYS: tuple[tuple[str, str], ...] = (
    ("alley_neon", "Rain-slick alley under buzzing kanji neon."),
    ("rooftop_zenith", "Helipad rooftop above a sleeping city grid."),
    ("sanctum_abyss", "Runed stone chamber lit by drifting will-o-wisps."),
)

BODY_KEYS: tuple[tuple[str, str], ...] = (
    ("base_street", "Athletic neutral stance — the default mannequin."),
    ("base_aegis", "Braced combat stance, weight forward."),
)

# ---------------------------------------------------------------------------
# Equippable catalog
# ---------------------------------------------------------------------------
ITEMS: tuple[ItemDef, ...] = (
    # --- legs -------------------------------------------------------------
    ItemDef(
        id="street_slacks",
        name="Street Slacks",
        slot=Slot.LEGS,
        rarity=Rarity.COMMON,
        defense=2,
        drip=1,
        price_credits=120,
        shop_pool="permanent",
        description="Plain black slacks. Nobody looks twice.",
    ),
    ItemDef(
        id="combat_boots",
        name="Combat Boots",
        slot=Slot.LEGS,
        rarity=Rarity.COMMON,
        defense=4,
        drip=1,
        price_credits=260,
        shop_pool="permanent",
        description="Steel toe, loud on wet pavement.",
    ),
    ItemDef(
        id="techwear_cargo",
        name="Techwear Cargos",
        slot=Slot.LEGS,
        rarity=Rarity.RARE,
        defense=7,
        drip=4,
        price_credits=850,
        shop_pool="permanent",
        description="Nineteen pockets, one of them hums.",
    ),
    ItemDef(
        id="street_slides",
        name="Arcane Slides with Socks",
        slot=Slot.LEGS,
        rarity=Rarity.RARE,
        defense=3,
        drip=9,
        price_credits=700,
        description="Disrespectful. Effective. Dripping.",
    ),
    ItemDef(
        id="shadow_wargreaves",
        name="Shadow Wargreaves",
        slot=Slot.LEGS,
        rarity=Rarity.LEGENDARY,
        defense=16,
        drip=8,
        price_credits=3200,
        price_soul_shards=6,
        description="They step where light refuses to.",
    ),
    # --- body / tops ------------------------------------------------------
    ItemDef(
        id="fitted_tee",
        name="Fitted Tee",
        slot=Slot.BODY,
        rarity=Rarity.COMMON,
        defense=3,
        drip=2,
        price_credits=150,
        shop_pool="permanent",
        description="Cheap, clean, correct.",
    ),
    ItemDef(
        id="tactical_hoodie",
        name="Tactical Hoodie",
        slot=Slot.BODY,
        rarity=Rarity.RARE,
        defense=8,
        drip=3,
        price_credits=780,
        shop_pool="permanent",
        description="Reinforced elbows, silent zipper.",
    ),
    ItemDef(
        id="leather_bomber",
        name="Leather Bomber",
        slot=Slot.BODY,
        rarity=Rarity.RARE,
        defense=10,
        drip=5,
        price_credits=940,
        description="Scuffed at the shoulders. Worn in, not out.",
    ),
    ItemDef(
        id="hunter_trench",
        name="Hunter's Trench",
        slot=Slot.BODY,
        rarity=Rarity.EPIC,
        defense=15,
        drip=9,
        price_credits=2400,
        price_soul_shards=3,
        shop_pool="permanent",
        description="Floor-length, cut for movement. The silhouette reads threat.",
    ),
    ItemDef(
        id="void_cuirass",
        name="Void Cuirass",
        slot=Slot.BODY,
        rarity=Rarity.LEGENDARY,
        defense=24,
        drip=12,
        price_credits=5200,
        price_soul_shards=10,
        description="Lighter than fabric. Darker than the room.",
    ),
    # --- head / hair ------------------------------------------------------
    ItemDef(
        id="street_fade",
        name="Street Fade",
        slot=Slot.HEAD,
        rarity=Rarity.COMMON,
        drip=2,
        price_credits=90,
        shop_pool="permanent",
        description="Sharp line, sharper confidence.",
    ),
    ItemDef(
        id="hood_up",
        name="Hood Up",
        slot=Slot.HEAD,
        rarity=Rarity.RARE,
        defense=2,
        drip=4,
        price_credits=520,
        description="Half your face gone. Whole your attitude stays.",
    ),
    ItemDef(
        id="raven_shag",
        name="Raven Shag",
        slot=Slot.HEAD,
        rarity=Rarity.EPIC,
        drip=7,
        price_credits=1600,
        price_soul_shards=2,
        description="Black hair that moves like it has opinions.",
    ),
    ItemDef(
        id="crown_of_shadows",
        name="Crown of Shadows",
        slot=Slot.HEAD,
        rarity=Rarity.LEGENDARY,
        drip=14,
        atk=3,
        price_credits=4200,
        price_soul_shards=8,
        description="Nobody crowns you. You just start glowing.",
    ),
    # --- accessory / face -------------------------------------------------
    ItemDef(
        id="tactical_goggles",
        name="Tactical Goggles",
        slot=Slot.ACCESSORY,
        rarity=Rarity.COMMON,
        defense=2,
        drip=2,
        price_credits=220,
        shop_pool="permanent",
        description="Amber lenses. Data lives in them.",
    ),
    ItemDef(
        id="half_mask",
        name="Ceramic Half-Mask",
        slot=Slot.ACCESSORY,
        rarity=Rarity.RARE,
        defense=4,
        drip=6,
        price_credits=690,
        description="Breathes easy. Says nothing.",
    ),
    ItemDef(
        id="arcane_eye_mark",
        name="Arcane Eye Marking",
        slot=Slot.ACCESSORY,
        rarity=Rarity.EPIC,
        atk=6,
        drip=8,
        price_credits=2100,
        price_soul_shards=3,
        description="A glyph under the left eye that reads your intent.",
    ),
    ItemDef(
        id="phantom_visage",
        name="Phantom Visage",
        slot=Slot.ACCESSORY,
        rarity=Rarity.LEGENDARY,
        atk=5,
        defense=6,
        drip=15,
        price_credits=5400,
        price_soul_shards=12,
        description="The face you had before the city took it.",
    ),
    # --- weapon -----------------------------------------------------------
    ItemDef(
        id="police_baton",
        name="Retired Baton",
        slot=Slot.WEAPON,
        rarity=Rarity.COMMON,
        atk=6,
        price_credits=180,
        shop_pool="permanent",
        description="Evidence locker, unofficially.",
    ),
    ItemDef(
        id="combat_knife",
        name="Tactical Combat Knife",
        slot=Slot.WEAPON,
        rarity=Rarity.COMMON,
        atk=11,
        price_credits=430,
        shop_pool="permanent",
        description="Nine inches of conversation ender.",
    ),
    ItemDef(
        id="neon_sai",
        name="Neon Sai",
        slot=Slot.WEAPON,
        rarity=Rarity.RARE,
        atk=18,
        drip=3,
        price_credits=1150,
        description="Glows cyan. Hums at low frequency.",
    ),
    ItemDef(
        id="shadow_katana",
        name="Shadow Katana",
        slot=Slot.WEAPON,
        rarity=Rarity.EPIC,
        atk=31,
        drip=6,
        price_credits=3400,
        price_soul_shards=5,
        shop_pool="permanent",
        description="Drawn from a scabbard that isn't there.",
    ),
    ItemDef(
        id="arcane_gauntlet",
        name="Arcane Gauntlet",
        slot=Slot.WEAPON,
        rarity=Rarity.LEGENDARY,
        atk=47,
        defense=8,
        drip=10,
        price_credits=6800,
        price_soul_shards=14,
        description="Closes around your fist and starts deciding.",
    ),
    # --- aura -------------------------------------------------------------
    ItemDef(
        id="ethereal_smoke",
        name="Ethereal Smoke",
        slot=Slot.AURA,
        rarity=Rarity.RARE,
        drip=6,
        price_credits=900,
        shop_pool="permanent",
        description="Curls off the shoulders like the street is smouldering.",
    ),
    ItemDef(
        id="lightning_crackle",
        name="Lightning Crackle",
        slot=Slot.AURA,
        rarity=Rarity.EPIC,
        atk=8,
        drip=9,
        price_credits=2600,
        price_soul_shards=4,
        description="Static in the teeth, ozone in the coat.",
    ),
    ItemDef(
        id="dark_flame",
        name="Dark Flame",
        slot=Slot.AURA,
        rarity=Rarity.LEGENDARY,
        atk=14,
        defense=6,
        drip=16,
        price_credits=5800,
        price_soul_shards=15,
        shop_pool="permanent",
        description="Fire that only ever consumes one thing.",
    ),
)

ITEMS_BY_ID: dict[str, ItemDef] = {item.id: item for item in ITEMS}


def starter_kit_ids() -> tuple[str, ...]:
    """Item ids every newly created player is given, so /me renders a full look."""
    return ("street_slacks", "fitted_tee", "street_fade")


def art_specs() -> list[tuple[str, str]]:
    """Every (layer_folder, layer_key) PNG the asset generator must produce."""
    specs: list[tuple[str, str]] = []
    specs.extend(("backgrounds", key) for key, _ in BACKGROUND_KEYS)
    specs.extend(("body", key) for key, _ in BODY_KEYS)
    specs.extend((item.folder, item.layer_key) for item in ITEMS)
    return specs


def art_descriptions() -> dict[tuple[str, str], str]:
    """Human-readable caption per art spec (used to seed the catalog + docs)."""
    out: dict[tuple[str, str], str] = {
        ("backgrounds", key): desc for key, desc in BACKGROUND_KEYS
    }
    out.update({("body", key): desc for key, desc in BODY_KEYS})
    out.update({(item.folder, item.layer_key): item.description for item in ITEMS})
    return out
