"""Economy: atomic wallet mutations, ledger audit, cooldowns, cash sinks.

All balance movement funnels through :func:`mutate`, which must be called
inside an open ``db.write()`` transaction.  That keeps the read-check-write
window atomic (``BEGIN IMMEDIATE``) *and* lets composite operations
(buy = debit + inventory insert + purchase row + ledger) commit or roll back
as a single unit.  :func:`grant` / :func:`spend` wrap it for callers that do
not already hold a transaction.

Every mutation appends exactly one ``ledger`` row carrying the reason
(:class:`ActivityKind`) and the post-mutation balances, so any account can be
reconciled without replaying history.
"""

from __future__ import annotations

import contextlib
import logging
import random
import secrets
import time
from dataclasses import dataclass

import aiosqlite

from config import settings
from database.connection import db
from models import ActivityKind
from services.game import GameError, InsufficientFunds, now

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cooldowns (in-memory; per-process, instant, no I/O in the hot path)
# ---------------------------------------------------------------------------

_cooldowns: dict[str, float] = {}


def _cooldown_key(user_id: int, action: str) -> str:
    return f"cooldown:{user_id}:{action}"


def cooldown_remaining(user_id: int, action: str) -> float:
    """Seconds left on ``action``'s cooldown (0 when ready)."""
    expires = _cooldowns.get(_cooldown_key(user_id, action))
    if expires is None:
        return 0.0
    return max(0.0, expires - time.monotonic())


def set_cooldown(user_id: int, action: str, seconds: int) -> None:
    if seconds <= 0:
        return
    _cooldowns[_cooldown_key(user_id, action)] = time.monotonic() + seconds


def clear_cooldowns() -> None:
    _cooldowns.clear()


class OnCooldown(GameError):
    def __init__(self, action: str, remaining: float) -> None:
        super().__init__(
            f"cooldown:{action}: {remaining:.0f}s left"
        )
        self.action = action
        self.remaining = remaining


def require_ready(user_id: int, action: str, seconds: int) -> None:
    """Raise :class:`OnCooldown` unless the action is allowed right now."""
    left = cooldown_remaining(user_id, action)
    if left > 0:
        raise OnCooldown(action, left)
    set_cooldown(user_id, action, seconds)


# ---------------------------------------------------------------------------
# Wallet primitives
# ---------------------------------------------------------------------------


async def _wallet_conn(
    conn: aiosqlite.Connection, user_id: int
) -> tuple[int, int]:
    cursor = await conn.execute(
        "SELECT credits, soul_shards FROM wallets WHERE user_id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise GameError(f"no wallet for player {user_id}")
    return row["credits"], row["soul_shards"]


async def mutate(
    conn: aiosqlite.Connection,
    user_id: int,
    *,
    credits: int = 0,
    shards: int = 0,
    kind: ActivityKind,
    ref: str = "",
) -> tuple[int, int]:
    """Apply a signed balance change inside an open transaction.

    Raises :class:`InsufficientFunds` (rolling the whole transaction back)
    rather than ever writing a negative balance.
    """
    balance_credits, balance_shards = await _wallet_conn(conn, user_id)
    new_credits = balance_credits + credits
    new_shards = balance_shards + shards
    if new_credits < 0:
        raise InsufficientFunds("credits", -credits, balance_credits)
    if new_shards < 0:
        raise InsufficientFunds("soul shards", -shards, balance_shards)

    await conn.execute(
        "UPDATE wallets SET credits = ?, soul_shards = ? WHERE user_id = ?",
        (new_credits, new_shards, user_id),
    )
    await conn.execute(
        """
        INSERT INTO ledger (user_id, kind, credits_delta, shards_delta,
                            balance_after_credits, balance_after_shards,
                            ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            kind.value,
            credits,
            shards,
            new_credits,
            new_shards,
            ref,
            now(),
        ),
    )
    return new_credits, new_shards


async def balances(user_id: int) -> tuple[int, int]:
    async with db.read() as conn:
        return await _wallet_conn(conn, user_id)


async def grant(
    user_id: int,
    *,
    credits: int = 0,
    shards: int = 0,
    kind: ActivityKind,
    ref: str = "",
) -> tuple[int, int]:
    """Convenience wrapper opening its own transaction."""
    async with db.write() as conn:
        return await mutate(
            conn, user_id, credits=credits, shards=shards, kind=kind, ref=ref
        )


async def spend(
    user_id: int,
    *,
    credits: int = 0,
    shards: int = 0,
    kind: ActivityKind,
    ref: str = "",
) -> tuple[int, int]:
    return await grant(
        user_id, credits=-credits, shards=-shards, kind=kind, ref=ref
    )


# ---------------------------------------------------------------------------
# Derived economy helpers
# ---------------------------------------------------------------------------


def drip_discount(drip: int) -> float:
    """Fractional price reduction from charisma, capped by config."""
    return min(settings.drip_discount_cap, drip * settings.drip_discount_per_point)


def discounted_price(price: int, drip: int) -> int:
    if price <= 0:
        return 0
    return max(1, round(price * (1.0 - drip_discount(drip))))


@dataclass(frozen=True, slots=True)
class ActivityResult:
    """Generic outcome the handlers render into chat."""

    success: bool
    headline: str
    detail: str = ""
    credits_delta: int = 0
    shards_delta: int = 0
    exp_gained: int = 0


# ---------------------------------------------------------------------------
# Daily claim
# ---------------------------------------------------------------------------

_DAY_SECONDS = 86_400


async def claim_daily(user_id: int, drip: int) -> ActivityResult:
    """One reward per rolling 24h. Drip adds a small bonus multiplier."""
    bonus = 1.0 + drip_discount(drip)  # charisma makes the daily bag fatter
    credits = int(settings.daily_claim_credits * bonus)
    shards = settings.daily_claim_shards
    current = now()

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT last_daily FROM players WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise GameError("start the game first (/start)")
        last = row["last_daily"]
        if current - last < _DAY_SECONDS:
            remaining = _DAY_SECONDS - (current - last)
            hours, minutes = divmod(remaining // 60, 60)
            return ActivityResult(
                False,
                "Already claimed",
                f"Next drop in {hours}h {minutes:02d}m.",
            )
        await conn.execute(
            "UPDATE players SET last_daily = ? WHERE user_id = ?", (current, user_id)
        )
        await mutate(
            conn,
            user_id,
            credits=credits,
            shards=shards,
            kind=ActivityKind.DAILY,
            ref="daily",
        )

    return ActivityResult(
        True,
        "Daily drop secured",
        f"+{credits} credits, +{shards} soul shards.",
        credits_delta=credits,
        shards_delta=shards,
        exp_gained=settings.daily_exp,
    )


# ---------------------------------------------------------------------------
# Work (energy -> credits faucet)
# ---------------------------------------------------------------------------


async def do_work(user_id: int, level: int, drip: int) -> ActivityResult:
    payout = settings.work_credits_base + settings.work_credits_per_level * level
    payout += int(payout * drip_discount(drip) * 0.5)
    # Small variance so the grind does not feel like a spreadsheet.
    payout = max(1, int(payout * random.uniform(0.9, 1.15)))

    from services.game import grant_exp_conn, spend_energy_conn

    async with db.write() as conn:
        await spend_energy_conn(conn, user_id, settings.work_energy_cost)
        await mutate(
            conn, user_id, credits=payout, kind=ActivityKind.WORK, ref="work"
        )
        await grant_exp_conn(conn, user_id, settings.work_exp)

    return ActivityResult(
        True,
        "Shift done",
        f"Corner shift paid {payout} credits "
        f"(-{settings.work_energy_cost} energy).",
        credits_delta=payout,
        exp_gained=settings.work_exp,
    )


# ---------------------------------------------------------------------------
# Street heist (stake -> payout with arrest risk)
# ---------------------------------------------------------------------------


def heist_success_chance(drip: int, stake: int) -> float:
    """Base chance + drip luck, tightened for big stakes (riskier scores)."""
    luck = drip * 0.0025
    size_penalty = 0.10 * (stake / max(1, settings.heist_stake_max))
    chance = settings.heist_base_success + luck - size_penalty
    return min(settings.heist_max_success, max(settings.heist_min_success, chance))


async def do_heist(user_id: int, stake: int, drip: int) -> ActivityResult:
    from services.game import grant_exp_conn, spend_energy_conn

    if not settings.heist_stake_min <= stake <= settings.heist_stake_max:
        raise GameError(
            f"stake must be {settings.heist_stake_min}-"
            f"{settings.heist_stake_max} credits"
        )

    chance = heist_success_chance(drip, stake)
    # House edge folded into the roll: win pays stake*(1+edge-adjusted odds).
    roll = secrets.randbelow(10_000) / 10_000.0
    arrested = False

    async with db.write() as conn:
        await spend_energy_conn(conn, user_id, settings.heist_energy_cost)
        # Escrow the stake first: losing the roll must not dodge the debit.
        await mutate(
            conn, user_id, credits=-stake, kind=ActivityKind.HEIST, ref="heist:stake"
        )

        if roll < chance:
            multiplier = 1.0 + (1.0 - settings.heist_house_edge)
            payout = int(stake * multiplier)
            await mutate(
                conn,
                user_id,
                credits=payout,
                kind=ActivityKind.HEIST,
                ref="heist:win",
            )
            await grant_exp_conn(conn, user_id, settings.work_exp + 5)
            return ActivityResult(
                True,
                "Score!",
                f"You cleared {payout} credits "
                f"(stake {stake}, net +{payout - stake}).",
                credits_delta=payout - stake,
                exp_gained=settings.work_exp + 5,
            )

        arrested = secrets.randbelow(10_000) / 10_000.0 < settings.heist_arrest_chance
        if arrested:
            # Already-exhausted players still owe the stake; only the energy
            # penalty is best-effort (caught inside the live transaction, so
            # the connection stays usable for the ledger row below).
            with contextlib.suppress(GameError):
                await spend_energy_conn(
                    conn, user_id, settings.heist_arrest_energy_cost
                )
            await mutate(
                conn,
                user_id,
                kind=ActivityKind.HEIST_ARREST,
                ref="heist:arrest",
            )
            detail = (
                f"They caught you on the way out. Stake {stake} gone, "
                f"-{settings.heist_arrest_energy_cost} energy overnight in holding."
            )
        else:
            detail = f"The mark noticed you. Stake {stake} lost, but you walked."

    return ActivityResult(
        False, "Busted" if arrested else "Blank", detail, credits_delta=-stake
    )


# ---------------------------------------------------------------------------
# Casino: dice & coinflip (strict house edge via payout multiplier)
# ---------------------------------------------------------------------------


def _payout(bet: int, edge: float, win_probability: float) -> int:
    """EV = win_probability * payout - bet = -edge * bet, by construction."""
    multiplier = (1.0 - edge) / win_probability
    return max(bet + 1, int(bet * multiplier))


async def casino_dice(user_id: int, bet: int, pick: str) -> ActivityResult:
    """2d6 high/low: high = 8+ (15/36), low = 6- (15/36); a 7 loses either way.

    Payout multiplier is derived from ``dice_house_edge`` so EV is exactly
    ``-edge * bet`` regardless of the threshold.
    """
    if pick not in ("high", "low"):
        raise GameError("pick must be high or low")
    die1 = secrets.randbelow(6) + 1
    die2 = secrets.randbelow(6) + 1
    total = die1 + die2
    won = total >= 8 if pick == "high" else total <= 6
    win_probability = 15 / 36  # symmetric thresholds (7 always loses)

    async with db.write() as conn:
        await mutate(
            conn, user_id, credits=-bet, kind=ActivityKind.CASINO, ref="dice:bet"
        )
        if won:
            payout = _payout(bet, settings.dice_house_edge, win_probability)
            await mutate(
                conn,
                user_id,
                credits=payout,
                kind=ActivityKind.CASINO,
                ref="dice:win",
            )
            return ActivityResult(
                True,
                f"🎲 {die1} + {die2} = {total} — you win!",
                f"Paid {payout} on a {bet} bet.",
                credits_delta=payout - bet,
            )
        return ActivityResult(
            False,
            f"🎲 {die1} + {die2} = {total} — house takes it.",
            f"-{bet} credits.",
            credits_delta=-bet,
        )


async def casino_coinflip(user_id: int, bet: int, pick: str) -> ActivityResult:
    if pick not in ("heads", "tails"):
        raise GameError("pick must be heads or tails")
    landed = "heads" if secrets.randbelow(2) == 0 else "tails"
    won = landed == pick

    async with db.write() as conn:
        await mutate(
            conn,
            user_id,
            credits=-bet,
            kind=ActivityKind.CASINO,
            ref="coinflip:bet",
        )
        if won:
            payout = _payout(bet, settings.coinflip_house_edge, 0.5)
            await mutate(
                conn,
                user_id,
                credits=payout,
                kind=ActivityKind.CASINO,
                ref="coinflip:win",
            )
            return ActivityResult(
                True,
                f"🪙 {landed} — you win!",
                f"Paid {payout} on a {bet} bet.",
                credits_delta=payout - bet,
            )
        return ActivityResult(
            False,
            f"🪙 {landed} — house takes it.",
            f"-{bet} credits.",
            credits_delta=-bet,
        )


def validate_bet(bet: int) -> int:
    if bet < settings.casino_min_bet:
        raise GameError(f"minimum bet is {settings.casino_min_bet} credits")
    if bet > settings.casino_max_bet:
        raise GameError(f"maximum bet is {settings.casino_max_bet} credits")
    return bet
