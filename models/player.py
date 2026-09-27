"""Player value objects.

These are plain dataclasses rather than an ORM: the project talks to SQLite
directly (``schema.sql`` + ``aiosqlite``), so ``models/`` is where *domain*
types live, separate from row-access code in ``services/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StatBlock:
    """An additive bundle of stat deltas (from gear, buffs, etc)."""

    atk: int = 0
    defense: int = 0
    drip: int = 0

    def __add__(self, other: StatBlock) -> StatBlock:
        return StatBlock(
            atk=self.atk + other.atk,
            defense=self.defense + other.defense,
            drip=self.drip + other.drip,
        )

    __radd__ = __add__

    @property
    def is_empty(self) -> bool:
        return self.atk == 0 and self.defense == 0 and self.drip == 0


@dataclass(frozen=True, slots=True)
class PlayerStats:
    """Fully resolved combat/economy stats used by battle and shop logic."""

    level: int
    atk: int
    defense: int
    drip: int
    energy: int
    max_energy: int

    @property
    def power(self) -> int:
        """Effective combat rating (ATK weighted over DEF) shown in the HUD."""
        return self.atk * 2 + self.defense


@dataclass(slots=True)
class Player:
    """A hydrated player row plus their resolved gear stats."""

    user_id: int
    display_name: str
    username: str | None = None
    level: int = 1
    exp: int = 0
    energy: int = 100
    energy_updated_at: int = 0
    base_atk: int = 10
    base_def: int = 8
    base_drip: int = 5
    last_daily: int = 0
    created_at: int = 0
    last_seen: int = 0
    gender: str = "نامشخص"
    age: int = 20
    skin_tone: str = "fair"
    eye_color: str = "amber"
    body_stance: str = "base_street"
    onboarding_completed: int = 0

    # Aggregates, filled in by services.game.load_player().
    gear: StatBlock = field(default_factory=StatBlock)
    # slot -> item id (None when the slot is empty)
    loadout: dict[str, str | None] = field(default_factory=dict)

    @property
    def atk(self) -> int:
        return max(1, self.base_atk + self.gear.atk)

    @property
    def defense(self) -> int:
        return max(1, self.base_def + self.gear.defense)

    @property
    def drip(self) -> int:
        return max(0, self.base_drip + self.gear.drip)

    @property
    def stats(self) -> PlayerStats:
        from config import settings

        return PlayerStats(
            level=self.level,
            atk=self.atk,
            defense=self.defense,
            drip=self.drip,
            energy=self.energy,
            max_energy=settings.max_energy,
        )

    @property
    def display_tag(self) -> str:
        """``@username`` when present, otherwise the display name."""
        return f"@{self.username}" if self.username else self.display_name
