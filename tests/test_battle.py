"""Battle engine: reproducibility, rounding, and no-deadlock guarantees."""

from __future__ import annotations

from config import settings
from models import Player
from services import battle


def _fighter(user_id: int, atk: int = 20, defense: int = 15, drip: int = 10) -> Player:
    return Player(
        user_id=user_id,
        display_name=f"P{user_id}",
        base_atk=atk,
        base_def=defense,
        base_drip=drip,
    )


def test_same_seed_same_result() -> None:
    a, b = battle.fighter_of(_fighter(1)), battle.fighter_of(_fighter(2))
    r1 = battle.simulate(a, b, seed=7)
    r2 = battle.simulate(a, b, seed=7)
    assert r1.winner_id == r2.winner_id
    assert [(s.damage, s.critical) for s in r1.strikes] == [
        (s.damage, s.critical) for s in r2.strikes
    ]
    assert r1.rounds == r2.rounds


def test_different_seeds_can_diverge() -> None:
    a, b = battle.fighter_of(_fighter(1)), battle.fighter_of(_fighter(2))
    outcomes = {
        battle.simulate(a, b, seed=s).winner_id for s in range(50)
    }
    assert len(outcomes) > 1, "seed never influences the outcome"


def test_always_has_a_winner() -> None:
    a, b = battle.fighter_of(_fighter(1)), battle.fighter_of(_fighter(2))
    for seed in range(100):
        result = battle.simulate(a, b, seed=seed)
        assert result.winner_id in (1, 2)


def test_round_count_matches_config() -> None:
    a, b = battle.fighter_of(_fighter(1)), battle.fighter_of(_fighter(2))
    result = battle.simulate(a, b, seed=3)
    assert 1 <= len(result.rounds) <= settings.duel_rounds


def test_hp_never_negative_in_log() -> None:
    a, b = battle.fighter_of(_fighter(1, atk=999)), battle.fighter_of(_fighter(2))
    result = battle.simulate(a, b, seed=11)
    assert all(s.defender_hp >= 0 for s in result.strikes)


def test_damage_always_positive() -> None:
    # Heavily outgeared defender still takes at least 1 damage (chip damage).
    tank = battle.fighter_of(_fighter(2, defense=500))
    weak = battle.fighter_of(_fighter(1, atk=1))
    result = battle.simulate(weak, tank, seed=5)
    assert all(s.damage >= 1 for s in result.strikes)
