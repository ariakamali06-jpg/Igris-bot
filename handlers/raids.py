"""Group raids: chat-activity counter, boss spawn, collaborative strikes.

The counter runs as a *message outer middleware* so it sees every group
message regardless of which command handler consumes the update — command
messages count too.  Boss portraits reuse the character compositor (dark
loadout + abyss background), so raids look native to the rest of the art.

Rate control: ``/strike`` is per-user cooldown gated, and HP-bar edits are
throttled to one per ``RAID_EDIT_INTERVAL`` seconds per raid (the final blow
always edits) so a busy group cannot trip Telegram's edit flood limits.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from config import settings
from handlers.common import answer_error, editable_message, esc, hydrate
from services import economy, raids
from services.compositor import RenderRequest, render_card

logger = logging.getLogger(__name__)
router = Router(name="raids")

RAID_EDIT_INTERVAL = 1.5  # seconds between HP-bar edits per raid
_last_raid_edit: dict[int, float] = {}

_BOSS_LOADOUT = {
    "legs": "shadow_wargreaves",
    "body": "void_cuirass",
    "head": "crown_of_shadows",
    "accessory": "phantom_visage",
    "weapon": "shadow_katana",
    "aura": "dark_flame",
}


def _hp_bar(hp: int, max_hp: int, width: int = 12) -> str:
    filled = round(width * hp / max(1, max_hp))
    return "█" * filled + "░" * (width - filled)


def _raid_markup(raid_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚔️ Strike Boss (-{settings.raid_dps_energy_cost}⚡)",
                    callback_data=f"raid:hit:{raid_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Leaderboard", callback_data=f"raid:top:{raid_id}"
                )
            ],
        ]
    )


async def _boss_card(spawn: raids.SpawnResult) -> bytes:
    """Render the boss portrait off-loop using the normal compositor."""
    request = RenderRequest(
        display_name=spawn.boss_name,
        username=None,
        level=1 + spawn.max_hp // 200,
        atk=spawn.max_hp,          # HUD "power" reads as the boss's threat
        defense=0,
        drip=0,
        loadout=dict(_BOSS_LOADOUT),
        background="sanctum_abyss",
        body="base_aegis",
    )
    return await asyncio.to_thread(render_card, request)


async def _send_spawn(bot, chat_id: int, spawn: raids.SpawnResult) -> None:
    photo = await _boss_card(spawn)
    caption = (
        f"🚨 <b>BOSS SPAWNED</b> — <b>{esc(spawn.boss_name)}</b>\n"
        f"<i>{esc(spawn.taunt)}</i>\n\n"
        f"{_hp_bar(spawn.hp, spawn.max_hp)} "
        f"<code>{spawn.hp}/{spawn.max_hp}</code> HP\n"
        f"Everyone in this chat can hit it. Split the loot by damage."
    )
    await bot.send_photo(
        chat_id,
        BufferedInputFile(photo, filename="boss.jpg"),
        caption=caption,
        reply_markup=_raid_markup(spawn.raid_id),
    )


# ---------------------------------------------------------------------------
# Message counter middleware
# ---------------------------------------------------------------------------


class GroupActivityMiddleware(BaseMiddleware):
    """Persistently count group messages and spawn bosses on threshold."""

    async def __call__(
        self,
        handler,
        event: TelegramObject,
        data: dict,
    ):
        # Only real group traffic counts (skip private chats and bots).
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if (
            chat is not None
            and chat.type in ("group", "supergroup")
            and user is not None
            and not user.is_bot
        ):
            try:
                spawn = await raids.bump_message(chat.id)
            except Exception:  # noqa: BLE001 - counting must never break handlers
                logger.exception("raid counter failed for chat %s", chat.id)
                spawn = None

            if spawn is not None:
                bot = data.get("bot")
                if bot is not None:
                    # Fire-and-forget: the fight must not wait on image IO.
                    asyncio.create_task(
                        _safe_spawn(bot, chat.id, spawn),
                        name=f"raid-spawn-{spawn.raid_id}",
                    )
        return await handler(event, data)


async def _safe_spawn(bot, chat_id: int, spawn: raids.SpawnResult) -> None:
    try:
        await _send_spawn(bot, chat_id, spawn)
    except Exception:  # noqa: BLE001
        logger.exception("failed to post raid %s in chat %s", spawn.raid_id, chat_id)


# ---------------------------------------------------------------------------
# Strike callback
# ---------------------------------------------------------------------------


def _fight_caption(raid: dict, strike: raids.StrikeResult, hitter: str) -> str:
    head = "💀 <b>BOSS DEFEATED</b>" if strike.cleared else "⚔️ Hit landed"
    crit = " 💥CRIT" if strike.critical else ""
    body = (
        f"{head} — <b>{esc(raid['boss_name'])}</b>{crit}\n"
        f"Damage: <b>{strike.damage:,}</b> by {esc(hitter)}\n"
        f"{_hp_bar(strike.hp_left, strike.max_hp)} "
        f"<code>{strike.hp_left}/{strike.max_hp}</code> HP"
    )
    if strike.cleared:
        lines = ["", "🏆 <b>Loot split by damage:</b>"]
        ranked = sorted(
            strike.shares.items(), key=lambda kv: kv[1].credits, reverse=True
        )
        for user_id, share in ranked[:8]:
            lines.append(
                f"• <code>{user_id}</code>: +{share.credits:,}cr "
                f"+{share.shards}◆ +{share.exp}xp"
            )
        body += "\n".join(lines)
    return body


@router.callback_query(F.data.startswith("raid:hit:"))
async def cb_strike(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    raid_id = int(call.data.rsplit(":", 1)[1])

    try:
        economy.require_ready(user.id, "raid", settings.cooldown_raid)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await raids.strike(raid_id, user.id, player.atk, player.drip)

        # Always ack exactly once — the client un-freezes the button here.
        await call.answer(
            f"{'💥 Crit! ' if result.critical else ''}-{result.damage:,} HP"
        )

        message = editable_message(call)
        if message is None:
            return
        # DB row is the source of truth for the boss name (no caption parsing).
        raid = await _raid_row(raid_id)
        boss_name = raid["boss_name"] if raid else "Boss"
        edit = result.cleared or _edit_due(raid_id)
        if edit:
            text = _fight_caption(
                {"boss_name": boss_name}, result, player.display_tag
            )
            markup = None if result.cleared else _raid_markup(raid_id)
            try:
                if message.photo:
                    await message.edit_caption(caption=text, reply_markup=markup)
                else:
                    await message.edit_text(text, reply_markup=markup)
            except Exception:  # noqa: BLE001 - "message is not modified" etc.
                logger.debug("raid message edit skipped", exc_info=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


def _edit_due(raid_id: int) -> bool:
    last = _last_raid_edit.get(raid_id, 0.0)
    nowtime = time.monotonic()
    if nowtime - last < RAID_EDIT_INTERVAL:
        return False
    _last_raid_edit[raid_id] = nowtime
    return True


@router.callback_query(F.data.startswith("raid:top:"))
async def cb_raid_top(call: CallbackQuery) -> None:
    if call.data is None:
        return
    raid_id = int(call.data.rsplit(":", 1)[1])
    rows = await raids.leaderboard_damage(raid_id, limit=10)
    if not rows:
        await call.answer("No hits yet.", show_alert=True)
        return
    lines = ["<b>📊 Damage leaderboard</b>"]
    for index, row in enumerate(rows, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(index, f"{index}.")
        lines.append(
            f"{medal} <code>{row['user_id']}</code> — "
            f"{row['damage']:,} dmg ({row['hits']} hits)"
        )
    await call.answer()  # ack, then swap the message content
    message = editable_message(call)
    if message is not None:
        try:
            await message.edit_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="◀️ Back to fight",
                                callback_data=f"raid:view:{raid_id}",
                            )
                        ]
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            logger.debug("leaderboard edit failed", exc_info=True)


@router.callback_query(F.data.startswith("raid:view:"))
async def cb_raid_view(call: CallbackQuery) -> None:
    """Return to the fight panel without spending energy."""
    await call.answer()
    message = editable_message(call)
    if call.data is None or message is None:
        return
    raid_id = int(call.data.rsplit(":", 1)[1])
    raid = await _raid_row(raid_id)
    if raid is None:
        await call.answer("That fight is over.", show_alert=True)
        return
    text = (
        f"🚨 <b>BOSS</b> — <b>{esc(raid['boss_name'])}</b>\n"
        f"{_hp_bar(raid['hp'], raid['max_hp'])} "
        f"<code>{raid['hp']}/{raid['max_hp']}</code> HP"
    )
    markup = None if raid["status"] != "active" else _raid_markup(raid_id)
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        logger.debug("raid view edit skipped", exc_info=True)


async def _raid_row(raid_id: int) -> dict | None:
    from database.connection import db

    row = await db.fetchone("SELECT * FROM raids WHERE id = ?", (raid_id,))
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# /boss — manual view of the active fight
# ---------------------------------------------------------------------------


@router.message(Command("boss", "raid"))
async def cmd_boss(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Raids only happen in groups.")
        return
    raid = await raids.active_raid(message.chat.id)
    if raid is None:
        await message.reply(
            "No boss active. Keep chatting — one shows up every "
            f"{settings.raid_spawn_min_messages}-{settings.raid_spawn_max_messages} "
            "messages."
        )
        return
    await message.reply(
        f"🚨 <b>{esc(raid['boss_name'])}</b>\n"
        f"{_hp_bar(raid['hp'], raid['max_hp'])} "
        f"<code>{raid['hp']}/{raid['max_hp']}</code> HP",
        reply_markup=_raid_markup(raid["id"]),
    )
