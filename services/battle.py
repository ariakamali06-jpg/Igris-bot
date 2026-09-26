"""Deterministic duel engine.

The battle is a pure function of ``(fighter stats, seed)``: the same inputs
always replay the same combat log, which is why the ``duels`` table stores a
seed — any dispute can be re-simulated offline.

Combat model (per config):
* Each fighter has an HP pool scaled from Defense.
* ``duel_rounds`` rounds; each round both fighters strike once.
* Damage = max(1, atk - defender_def/2) with +/-25% variance.
* Critical chance = base + drip * per-point bonus; crits hit
  ``duel_critical_multiplier`` and the log flags them for flair.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from config import settings
from models import Player


@dataclass(frozen=True, slots=True)
class Fighter:
    user_id: int
    name: str
    atk: int
    defense: int
    drip: int

    @property
    def max_hp(self) -> int:
        return 60 + self.defense * 4


@dataclass(slots=True)
class Strike:
    round: int
    attacker_id: int
    defender_id: int
    damage: int
    critical: bool
    defender_hp: int

    def line(self, names: dict[int, str]) -> str:
        attacker = names[self.attacker_id]
        defender = names[self.defender_id]
        verb = "💥 CRITICAL" if self.critical else "hit"
        return (
            f"R{self.round} · {attacker} {verb} {defender} "
            f"for {self.damage} ({self.defender_hp} HP left)"
        )


@dataclass(slots=True)
class BattleResult:
    winner_id: int | None
    strikes: list[Strike] = field(default_factory=list)
    rounds: list[str] = field(default_factory=list)
    xp_winner: int = 0
    xp_loser: int = 0

    @property
    def draw(self) -> bool:
        return self.winner_id is None

    def log_lines(self, names: dict[int, str]) -> list[str]:
        return [strike.line(names) for strike in self.strikes]


def fighter_of(player: Player) -> Fighter:
    return Fighter(
        user_id=player.user_id,
        name=player.display_tag,
        atk=player.atk,
        defense=player.defense,
        drip=player.drip,
    )


def _strike(
    rng: random.Random,
    attacker: Fighter,
    defender: Fighter,
    hp: dict[int, int],
    round_no: int,
) -> Strike:
    crit_chance = min(
        0.6, settings.duel_critical_base + attacker.drip * settings.duel_critical_drip_bonus
    )
    critical = rng.random() < crit_chance
    base = max(1, attacker.atk - defender.defense // 2)
    variance = rng.uniform(0.75, 1.25)
    damage = int(base * variance)
    if critical:
        damage = int(damage * settings.duel_critical_multiplier)
    damage = max(1, damage)
    hp[defender.user_id] = max(0, hp[defender.user_id] - damage)
    return Strike(
        round=round_no,
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        damage=damage,
        critical=critical,
        defender_hp=hp[defender.user_id],
    )


def simulate(a: Fighter, b: Fighter, seed: int) -> BattleResult:
    """Replay a full duel. Pure function of (stats, seed) — no I/O."""
    rng = random.Random(seed)
    hp = {a.user_id: a.max_hp, b.user_id: b.max_hp}
    result = BattleResult(winner_id=None)

    # Opening order is seeded so neither player is systematically first.
    first, second = (a, b) if rng.random() < 0.5 else (b, a)

    for round_no in range(1, settings.duel_rounds + 1):
        for attacker, defender in ((first, second), (second, first)):
            if hp[attacker.user_id] <= 0 or hp[defender.user_id] <= 0:
                continue
            result.strikes.append(_strike(rng, attacker, defender, hp, round_no))

        a_hp, b_hp = hp[a.user_id], hp[b.user_id]
        if a_hp == b_hp:
            label = f"Round {round_no}: deadlock — {a.name} {a_hp} vs {b.name} {b_hp}"
        else:
            leader = a if a_hp > b_hp else b
            label = (
                f"Round {round_no}: {leader.name} controls the exchange "
                f"({max(a_hp, b_hp)} vs {min(a_hp, b_hp)})"
            )
        result.rounds.append(label)

        if a_hp == 0 or b_hp == 0:
            break

    a_hp, b_hp = hp[a.user_id], hp[b.user_id]
    if a_hp == b_hp:
        # Sudden death: one seeded coin flip keeps draws impossible but fair.
        result.winner_id = a.user_id if rng.random() < 0.5 else b.user_id
    else:
        result.winner_id = a.user_id if a_hp > b_hp else b.user_id

    result.xp_winner = settings.duel_exp_win
    result.xp_loser = settings.duel_exp_loss
    return result
