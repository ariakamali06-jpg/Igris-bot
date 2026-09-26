"""/daily, /work, /heist and the mini-casino (dice, coinflip).

Each command follows the same shape: cooldown gate → domain call → one
rendered reply.  Bets are parsed from the command args with hard clamps from
config, and every outcome (win or loss) already committed its ledger row
before we format the message, so chat text can never disagree with balances.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from handlers.common import answer_error, energy_bar, esc, hydrate
from services import economy
from services.game import GameError, exp_to_next

logger = logging.getLogger(__name__)
router = Router(name="economy")


def _activity_card(result: economy.ActivityResult) -> str:
    icon = "✅" if result.success else "❌"
    lines = [f"{icon} <b>{esc(result.headline)}</b>"]
    if result.detail:
        lines.append(esc(result.detail))
    bits = []
    if result.credits_delta:
        sign = "+" if result.credits_delta > 0 else ""
        bits.append(f"{sign}{result.credits_delta:,}cr")
    if result.shards_delta:
        sign = "+" if result.shards_delta > 0 else ""
        bits.append(f"{sign}{result.shards_delta}◆")
    if result.exp_gained:
        bits.append(f"+{result.exp_gained}xp")
    if bits:
        lines.append(" · ".join(bits))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /daily
# ---------------------------------------------------------------------------


@router.message(Command("daily", "claim"))
async def cmd_daily(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "daily", settings.cooldown_daily)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.claim_daily(player.user_id, player.drip)
        if result.success and result.exp_gained:
            from services.game import grant_exp

            await grant_exp(player.user_id, result.exp_gained)
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# /work
# ---------------------------------------------------------------------------


@router.message(Command("work", "shift"))
async def cmd_work(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "work", settings.cooldown_work)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.do_work(
            player.user_id, player.level, player.drip
        )
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# /heist
# ---------------------------------------------------------------------------


@router.message(Command("heist", "rob"))
async def cmd_heist(message: Message, command: CommandObject) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "heist", settings.cooldown_heist)
        player = await hydrate(user.id, user.full_name, user.username)

        args = (command.args or "").split()
        if not args or not args[0].isdigit():
            raise GameError(
                f"usage: /heist <stake> — "
                f"{settings.heist_stake_min:,}-"
                f"{settings.heist_stake_max:,} credits "
                f"(success odds improve with DRIP)"
            )
        stake = int(args[0])
        result = await economy.do_heist(player.user_id, stake, player.drip)
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Casino
# ---------------------------------------------------------------------------


@router.message(Command("dice"))
async def cmd_dice(message: Message, command: CommandObject) -> None:
    await _casino_command(message, command, game="dice")


@router.message(Command("coinflip", "flip"))
async def cmd_coinflip(message: Message, command: CommandObject) -> None:
    await _casino_command(message, command, game="coinflip")


async def _casino_command(message: Message, command: CommandObject, game: str) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "casino", settings.cooldown_casino)
        player = await hydrate(user.id, user.full_name, user.username)

        args = (command.args or "").split()
        if game == "dice":
            if len(args) < 2 or not args[0].isdigit() or args[1] not in ("high", "low"):
                raise GameError(
                    f"usage: /dice <bet> <high|low> — "
                    f"high = 8+, low = 6- (7 loses both ways). "
                    f"Bet {settings.casino_min_bet:,}-{settings.casino_max_bet:,}."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_dice(player.user_id, bet, args[1])
        else:
            if len(args) < 2 or not args[0].isdigit() or args[1] not in ("heads", "tails"):
                raise GameError(
                    f"usage: /coinflip <bet> <heads|tails> — "
                    f"Bet {settings.casino_min_bet:,}-{settings.casino_max_bet:,}."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_coinflip(player.user_id, bet, args[1])

        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# /balance, /stats
# ---------------------------------------------------------------------------


@router.message(Command("balance", "bal", "wallet", "money"))
async def cmd_balance(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        credits, shards = await economy.balances(player.user_id)
        discount = economy.drip_discount(player.drip)
        await message.reply(
            f"💰 <b>{esc(player.display_tag)}</b>\n"
            f"Credits: <b>{credits:,}</b>\n"
            f"Soul shards: <b>{shards}</b>\n"
            f"Shop discount from DRIP: <b>{discount:.0%}</b>"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(Command("stats", "level"))
async def cmd_stats(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        stats = player.stats
        await message.reply(
            f"📊 <b>{esc(player.display_tag)}</b> — Level {player.level}\n"
            f"⚡ {energy_bar(player.energy, stats.max_energy)} "
            f"{player.energy}/{stats.max_energy} "
            f"(+{settings.energy_regen_per_minute:g}/min)\n"
            f"⚔️ ATK {player.atk} · 🛡 DEF {player.defense} · 💎 DRIP {player.drip}\n"
            f"✨ {player.exp}/{exp_to_next(player.level)} EXP to next level"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Quick action buttons (posted under the character card)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "act:work")
async def cb_work(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "work", settings.cooldown_work)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.do_work(player.user_id, player.level, player.drip)
        await call.answer(
            f"{result.headline}: {result.detail}", show_alert=True
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data == "act:daily")
async def cb_daily(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "daily", settings.cooldown_daily)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.claim_daily(player.user_id, player.drip)
        if result.success and result.exp_gained:
            from services.game import grant_exp

            await grant_exp(player.user_id, result.exp_gained)
        await call.answer(f"{result.headline}: {result.detail}", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


def _help_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎴 My card", callback_data="act:me"),
                InlineKeyboardButton(text="🏪 Shop", callback_data="shop:0"),
            ],
            [
                InlineKeyboardButton(text="📅 Daily", callback_data="act:daily"),
                InlineKeyboardButton(text="💼 Work", callback_data="act:work"),
            ],
        ]
    )


@router.message(Command("help", "start"))
async def cmd_help(message: Message) -> None:
    await message.reply(
        "<b>URBAN FANTASY RPG</b> — group street-crawler bot\n\n"
        "<b>Character</b>\n"
        "• /me — render your character card\n"
        "• /inventory — gear up (updates the card)\n"
        "• /stats — raw numbers without the art\n\n"
        "<b>Combat</b>\n"
        "• /duel [stake] — reply to someone to challenge them\n"
        "• /boss — view the active group raid\n"
        "• Raids spawn every "
        f"{settings.raid_spawn_min_messages}-{settings.raid_spawn_max_messages} "
        "group messages\n\n"
        "<b>Economy</b>\n"
        "• /balance · /daily · /work · /heist <stake>\n"
        "• /dice &lt;bet&gt; &lt;high|low&gt; · /coinflip &lt;bet&gt; &lt;heads|tails&gt;\n"
        "• /shop — daily rotating boutique\n\n"
        "⚡ Energy regenerates over time; 💎 DRIP boosts luck and discounts.",
        reply_markup=_help_markup(),
    )
