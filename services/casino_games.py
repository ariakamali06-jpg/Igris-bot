"""Ocean port phase 2 — single-player house games of the Tiramix arcades.

Four games live here: :func:`slots` (کازینو, weighted three-reel machine),
:func:`rps` (قل‌سنگ, throws against the house), :func:`guess` (شانس, the
hidden number) and the ``قفل`` safe trio (:func:`safe_start`,
:func:`safe_attempt`, :func:`safe_status`).

Every game escrows the stake through :func:`economy.mutate` inside one
``db.write()`` transaction *before* the outcome is decided, so a losing roll
can never dodge the debit, and every payout writes its own ledger row.
Odds, multipliers, bet bands and attempt caps all come from ``config.settings``
— nothing gameplay-facing is hardcoded here.
"""

from __future__ import annotations

import random
import secrets
from collections import Counter
from typing import Any

from config import SLOT_SYMBOLS, settings
from database.connection import db
from models.enums import ActivityKind
from services import economy
from services.game import GameError, now

# Cooldown action names (``economy.require_ready`` keys).
ACTION_SLOT = "slot"
ACTION_RPS = "rps"
ACTION_GUESS = "guess"
ACTION_SAFE_START = "safe_start"
ACTION_SAFE_ATTEMPT = "safe_attempt"

RPS_MOVES: tuple[str, ...] = ("سنگ", "کاغذ", "قیچی")
_RPS_ALIASES: dict[str, str] = {
    "سنگ": "سنگ",
    "rock": "سنگ",
    "کاغذ": "کاغذ",
    "paper": "کاغذ",
    "قیچی": "قیچی",
    "scissors": "قیچی",
}
_RPS_BEATS: dict[str, str] = {"سنگ": "قیچی", "قیچی": "کاغذ", "کاغذ": "سنگ"}
_RPS_EMOJI = {"سنگ": "🪨", "کاغذ": "📜", "قیچی": "✂️"}


def check_bet(bet: int) -> int:
    """Clamp-validate a house-game stake against the config bet band."""
    if bet < settings.casino_min_bet or bet > settings.casino_max_bet:
        raise GameError(
            f"شرط باید بین {settings.casino_min_bet:,} تا "
            f"{settings.casino_max_bet:,} سکه باشد، همسایه."
        )
    return bet


def normalize_rps_move(raw: str) -> str:
    """Map a player's throw (Persian or English) onto the canonical move."""
    move = _RPS_ALIASES.get(raw.strip().casefold()) or _RPS_ALIASES.get(raw.strip())
    if move is None:
        raise GameError(
            "قل‌سنگ فقط سه حالت دارد: <code>سنگ</code>، <code>کاغذ</code> یا "
            "<code>قیچی</code>.\nمثال: <code>قل‌سنگ 200 قیچی</code>"
        )
    return move


# ---------------------------------------------------------------------------
# کازینو — slot machine
# ---------------------------------------------------------------------------


def draw_reels(spin: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """One pull of the machine; ``spin`` lets tests rig the reels."""
    if spin is not None:
        if len(spin) != 3:
            raise GameError("یک اسپین سه‌چرخه لازمه.")
        return spin
    symbols = [row["symbol"] for row in SLOT_SYMBOLS]
    weights = [int(row["weight"]) for row in SLOT_SYMBOLS]
    return tuple(random.choices(symbols, weights=weights, k=3))


def slot_payout(bet: int, reels: tuple[str, ...]) -> int:
    """Three of a kind pays the symbol's multiplier, a pair the pair rate."""
    counts = Counter(reels)
    for symbol, count in counts.items():
        if count == 3:
            multiplier = next(
                float(row["three"]) for row in SLOT_SYMBOLS if row["symbol"] == symbol
            )
            return int(bet * multiplier)
    if any(count >= 2 for count in counts.values()):
        return int(bet * settings.slot_pair_multiplier)
    return 0


async def slots(user_id: int, bet: int, *, spin: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Spin the three reels for ``bet``. ``spin`` rigs the outcome in tests."""
    check_bet(bet)
    reels = draw_reels(spin)
    payout = slot_payout(bet, reels)
    counts = Counter(reels)
    three = next((symbol for symbol, count in counts.items() if count == 3), None)
    pair = three is None and any(count >= 2 for count in counts.values())

    async with db.write() as conn:
        await economy.mutate(conn, user_id, credits=-bet, kind=ActivityKind.SLOT, ref="slot:bet")
        if payout:
            await economy.mutate(
                conn, user_id, credits=payout, kind=ActivityKind.SLOT, ref="slot:win"
            )

    shown = " | ".join(reels)
    if three is not None:
        headline = f"🎰 {shown} — سه‌تا {three}! چرخ‌ها بردنت! 🎉"
        detail = f"شرط {bet:,} سکه، جایزه {payout:,} سکه (سود خالص: +{payout - bet:,})."
    elif pair:
        headline = f"🎰 {shown} — جفت اومد! 💸"
        detail = f"شرط {bet:,} سکه، جایزه {payout:,} سکه (سود: +{payout - bet:,})."
    else:
        headline = f"🎰 {shown} — بست."
        detail = f"کازینو {bet:,} سکه رو برداشت؛ شب بعد شانس با توئه."
    return {
        "success": payout > 0,
        "message": f"{headline}\n{detail}",
        "reels": reels,
        "payout": payout,
        "delta": payout - bet,
    }


# ---------------------------------------------------------------------------
# قل‌سنگ — rock-paper-scissors against the house
# ---------------------------------------------------------------------------


def rps_payout(bet: int) -> int:
    """Fair 3-way payout trimmed by the configured house edge."""
    multiplier = (1.0 - settings.rps_house_edge) / settings.rps_win_prob
    return max(bet + 1, int(bet * multiplier))


async def rps(
    user_id: int,
    bet: int,
    move: str,
    *,
    house_move: str | None = None,
) -> dict[str, Any]:
    """Throw ``move`` against the house; a draw pushes the stake back."""
    check_bet(bet)
    player_move = normalize_rps_move(move)
    house = house_move if house_move in RPS_MOVES else secrets.choice(RPS_MOVES)

    if player_move == house:
        async with db.write() as conn:
            await economy.mutate(
                conn, user_id, credits=-bet, kind=ActivityKind.RPS, ref="rps:bet"
            )
            await economy.mutate(
                conn, user_id, credits=bet, kind=ActivityKind.RPS, ref="rps:push"
            )
        return {
            "success": True,
            "message": (
                f"🪨 قل‌سنگ: تو {_RPS_EMOJI[player_move]}، خانه {_RPS_EMOJI[house]} — مساوی!\n"
                f"شرط {bet:,} سکه سر جاش موند؛ کف زدنِ کوچه بدون خون‌ریزی تموم شد."
            ),
            "payout": bet,
            "delta": 0,
        }

    if _RPS_BEATS[player_move] == house:
        payout = rps_payout(bet)
        async with db.write() as conn:
            await economy.mutate(
                conn, user_id, credits=-bet, kind=ActivityKind.RPS, ref="rps:bet"
            )
            await economy.mutate(
                conn, user_id, credits=payout, kind=ActivityKind.RPS, ref="rps:win"
            )
        return {
            "success": True,
            "message": (
                f"✊ {_RPS_EMOJI[player_move]} بر {_RPS_EMOJI[house]} — خانه رو شکستی! 🏆\n"
                f"شرط {bet:,} سکه، جایزه {payout:,} سکه (سود: +{payout - bet:,})."
            ),
            "payout": payout,
            "delta": payout - bet,
        }

    async with db.write() as conn:
        await economy.mutate(conn, user_id, credits=-bet, kind=ActivityKind.RPS, ref="rps:bet")
    return {
        "success": False,
        "message": (
            f"🪨 {_RPS_EMOJI[player_move]} خورد به {_RPS_EMOJI[house]} — باختی!\n"
            f"خانه {bet:,} سکه رو لای لبش گذاشت."
        ),
        "payout": 0,
        "delta": -bet,
    }


# ---------------------------------------------------------------------------
# شانس — guess the hidden number
# ---------------------------------------------------------------------------


async def guess(
    user_id: int, bet: int, number: int, *, secret: int | None = None
) -> dict[str, Any]:
    """Pick a number in ``1..guess_number_max``; a hit pays the multiplier."""
    check_bet(bet)
    ceiling = settings.guess_number_max
    if not 1 <= number <= ceiling:
        raise GameError(
            f"عدد باید بین ۱ تا {ceiling} باشد.\nمثال: <code>شانس 300 7</code>"
        )
    hidden = secret if secret is not None else secrets.randbelow(ceiling) + 1
    won = number == hidden
    payout = int(bet * settings.guess_win_multiplier) if won else 0

    async with db.write() as conn:
        await economy.mutate(conn, user_id, credits=-bet, kind=ActivityKind.GUESS, ref="guess:bet")
        if won:
            await economy.mutate(
                conn, user_id, credits=payout, kind=ActivityKind.GUESS, ref="guess:win"
            )

    if won:
        return {
            "success": True,
            "message": (
                f"🎲 عدد پنهان {hidden} بود — درست زدی! 💎\n"
                f"شرط {bet:,} سکه، جایزه {payout:,} سکه (سود: +{payout - bet:,})."
            ),
            "payout": payout,
            "delta": payout - bet,
        }
    return {
        "success": False,
        "message": (
            f"🎲 عدد پنهان {hidden} بود و تو {number} زدی — نخورد! 😶\n"
            f"{bet:,} سکه رو کوچه جمع کرد."
        ),
        "payout": 0,
        "delta": -bet,
    }


# ---------------------------------------------------------------------------
# قفل — the 3-digit safe
# ---------------------------------------------------------------------------


def draw_safe_code() -> str:
    """A fresh 3-digit code from the configured band (no leading zero)."""
    span = settings.safe_code_max - settings.safe_code_min + 1
    return str(settings.safe_code_min + secrets.randbelow(span))


def exact_digits(code: str, guess: str) -> int:
    """Digits sitting in their own slot — the lock's 'X رقم درست'."""
    return sum(1 for c, g in zip(code, guess, strict=True) if c == g)


def _check_code(token: str) -> str:
    if not (token.isdigit() and len(token) == 3):
        raise GameError(
            "کد قفل سه رقمیه.\n"
            f"برای باز کردن: <code>قفل {token}</code> → مثلاً <code>قفل 482</code>"
        )
    value = int(token)
    if not settings.safe_code_min <= value <= settings.safe_code_max:
        raise GameError(
            f"کد قفل باید بین {settings.safe_code_min} تا {settings.safe_code_max} باشد."
        )
    return token


async def active_lock(user_id: int) -> dict[str, Any] | None:
    """The player's live lock row (``None`` when no round is in play)."""
    row = await db.fetchone(
        "SELECT * FROM safe_locks WHERE user_id = ? AND status = 'active'", (user_id,)
    )
    return dict(row) if row else None


async def safe_start(user_id: int, bet: int, *, code: str | None = None) -> dict[str, Any]:
    """Escrow ``bet`` and hide a fresh 3-digit code in the lock."""
    check_bet(bet)
    if await active_lock(user_id) is not None:
        raise GameError(
            "یه قفل بازِ جلومونه؛ اول همین رو بشکن یا بذار بسته بشه.\n"
            "وضعیت: <code>قفل</code> · تلاش: <code>قفل [کد سه‌رقمی]</code>"
        )
    hidden = code if code is not None else draw_safe_code()

    async with db.write() as conn:
        await economy.mutate(
            conn, user_id, credits=-bet, kind=ActivityKind.SAFE, ref="safe:escrow"
        )
        await conn.execute(
            """
            INSERT INTO safe_locks (user_id, stake, code, attempts, status, created_at)
            VALUES (?, ?, ?, 0, 'active', ?)
            ON CONFLICT (user_id) DO UPDATE SET
                stake = excluded.stake,
                code = excluded.code,
                attempts = 0,
                status = 'active',
                payout = 0,
                created_at = excluded.created_at,
                resolved_at = NULL
            """,
            (user_id, bet, hidden, now()),
        )
    return {
        "success": True,
        "message": (
            f"🔐 گاوصندوقِ کوچه قفل شد — {bet:,} سکه زیر زبونِ آهنی پنهونه.\n"
            f"کد سه‌رقمی رو باید بشکنی؛ {settings.safe_max_attempts} تا تلاش داری.\n"
            "تلاش: <code>قفل [کد]</code> · وضعیت: <code>قفل</code>\n"
            "<i>هر چی رقم درست‌تر باشه، صداش قشنگ‌تره…</i>"
        ),
        "stake": bet,
    }


async def safe_attempt(user_id: int, token: str) -> dict[str, Any]:
    """Try a 3-digit code against the live lock; feedback is 'X رقم درست'."""
    lock = await active_lock(user_id)
    if lock is None:
        raise GameError(
            "قفل بازی در کار نیست. اول سپر بذار: <code>قفل [مبلغ]</code>"
        )
    guess_token = _check_code(token)
    attempts = int(lock["attempts"]) + 1
    hits = exact_digits(str(lock["code"]), guess_token)

    if hits == 3:
        payout = int(int(lock["stake"]) * settings.safe_payout_multiplier)
        async with db.write() as conn:
            await conn.execute(
                "UPDATE safe_locks SET attempts = ?, status = 'opened', payout = ?, "
                "resolved_at = ? WHERE user_id = ?",
                (attempts, payout, now(), user_id),
            )
            await economy.mutate(
                conn, user_id, credits=payout, kind=ActivityKind.SAFE, ref="safe:open"
            )
        return {
            "success": True,
            "message": (
                f"💥 {guess_token} — {hits} رقم درست! قفل باز شد! 🎉\n"
                f"{payout:,} سکه از دلِ آهن دراومد (سود: +{payout - int(lock['stake']):,})."
            ),
            "payout": payout,
            "hits": hits,
        }

    if attempts >= settings.safe_max_attempts:
        stake = int(lock["stake"])
        async with db.write() as conn:
            await conn.execute(
                "UPDATE safe_locks SET attempts = ?, status = 'failed', "
                "resolved_at = ? WHERE user_id = ?",
                (attempts, now(), user_id),
            )
        return {
            "success": False,
            "message": (
                f"🔒 {guess_token} — {hits} رقم درست.\n"
                f"تلاش‌ها تموم شد؛ قفل بسته شد و {stake:,} سکه سوخت."
            ),
            "payout": 0,
            "hits": hits,
        }

    left = settings.safe_max_attempts - attempts
    # Persist the attempt counter — without this the lock never jams shut.
    async with db.write() as conn:
        await conn.execute(
            "UPDATE safe_locks SET attempts = ? WHERE user_id = ? AND status = 'active'",
            (attempts, user_id),
        )
    return {
        "success": False,
        "message": (
            f"🔑 {guess_token} — {hits} رقم درست.\n"
            f"{left} تلاش مونده؛ دست لرزه نگیر، دیوار صبوری می‌کنه."
        ),
        "payout": 0,
        "hits": hits,
    }


async def safe_status(user_id: int) -> dict[str, Any]:
    """Live lock state — never leaks the code."""
    lock = await active_lock(user_id)
    if lock is None:
        return {
            "success": False,
            "message": (
                "🔓 قفلی در کار نیست. سراغ گاوصندوق برو: <code>قفل [مبلغ]</code>\n"
                f"هر {settings.safe_max_attempts} تلاش یک شانس؛ باز شدی "
                f"{settings.safe_payout_multiplier:g} برابر می‌شی."
            ),
        }
    left = settings.safe_max_attempts - int(lock["attempts"])
    return {
        "success": True,
        "message": (
            f"🔐 قفل فعال — شرط: <b>{int(lock['stake']):,}</b> سکه\n"
            f"تلاش باقی‌مانده: <b>{left}</b> از {settings.safe_max_attempts}\n"
            "کد را بزن: <code>قفل [سه رقم]</code>"
        ),
        "attempts_left": left,
    }
