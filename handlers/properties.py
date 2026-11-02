"""املاک / نیرو / نوچه — the passive-economy commands (buy, upgrade, collect).

Provides both text commands and interactive inline glass buttons for buying,
upgrading, and collecting passive income from properties and hired crew.
"""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import PROPERTY_CATALOG, WORKER_CATALOG, settings
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    extract_args,
    hydrate,
)
from models import Player
from services import economy, properties
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="properties")

PROPERTIES_WORDS = {"املاک", "ملک", "املاک و مستغلات", "دارایی"}
CREW_WORDS = {"نیرو", "نوچه", "خدمه", "بادیگارد", "نوچه‌ها", "crew", "hire"}

_BUY_ACTIONS = {"خرید", "buy", "purchase", "استخدام"}
_UPGRADE_ACTIONS = {"ارتقا", "ارتقاء", "upgrade", "levelup"}
_COLLECT_ACTIONS = {"وصول", "collect", "برداشت"}

_USAGE = (
    "🏘 <b>دستورات املاک و نوچه‌ها</b>\n"
    "• <code>املاک</code> — فهرست ملک‌های فروشی، درآمد انباشته و دکمه‌های خرید\n"
    "• <code>املاک خرید [نام]</code> — خرید ملک جدید\n"
    "• <code>املاک ارتقا [نام]</code> — ارتقای لول (+۳۰٪ درآمد)\n"
    "• <code>املاک وصول</code> — جمع‌آوری درآمد انباشته\n"
    "• <code>نوچه</code> / <code>نیرو</code> — استخدام بادیگارد و خدمه مسلح"
)


def _property_markup(owned_rows: list) -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای خرید و ارتقای املاک شهر تیرامیکس."""
    owned_map = {r["name"]: r for r in owned_rows}
    buttons: list[list[InlineKeyboardButton]] = []

    for entry in PROPERTY_CATALOG:
        name = entry["name"]
        price = entry["price"]
        if name in owned_map:
            lvl = owned_map[name]["level"]
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"⬆️ ارتقای {name} (لول {lvl})",
                        callback_data=f"prop:upg:{name}",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"🏢 خرید {name} ({price:,} رکسار)",
                        callback_data=f"prop:buy:{name}",
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                text="💰 وصول درآمد انباشته", callback_data="prop:collect:property"
            ),
            InlineKeyboardButton(
                text="👥 مدیریت نوچه‌ها و نیروها", callback_data="prop:view:worker"
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="prop:view:property"),
            InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _worker_markup(owned_rows: list) -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای استخدام نوچه‌ها و نیروهای مافیا."""
    owned_names = {r["name"] for r in owned_rows}
    buttons: list[list[InlineKeyboardButton]] = []

    for entry in WORKER_CATALOG:
        name = entry["name"]
        price = entry["price"]
        if name in owned_names:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {name} (استخدام شده)",
                        callback_data="noop",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"👥 استخدام {name} ({price:,} رکسار)",
                        callback_data=f"prop:buy_w:{name}",
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(text="💰 وصول درآمد انباشته", callback_data="prop:collect:worker"),
            InlineKeyboardButton(text="🏢 مدیریت املاک", callback_data="prop:view:property"),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="prop:view:worker"),
            InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _render_view(player: Player, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    res = await properties.list_estate(player, kind)
    owned = res.get("owned", [])
    markup = _property_markup(owned) if kind == properties.KIND_PROPERTY else _worker_markup(owned)
    return res["message"], markup


async def _reply(message: Message, command: CommandObject | None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            text, markup = await _render_view(player, properties.KIND_PROPERTY)
            await message.reply(text, reply_markup=markup)
        else:
            res = await _run_action(user.id, player, args)
            await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


async def _run_action(user_id: int, player: Player, args: list[str]) -> dict:
    action = args[0].casefold()
    name = " ".join(args[1:]).strip()

    if action in _COLLECT_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_COLLECT, settings.cooldown_property_collect
        )
        return await properties.collect(player)

    if action in _BUY_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_BUY, settings.cooldown_property_buy
        )
        if not name:
            raise GameError(_USAGE)
        return await properties.buy_asset(player, properties.KIND_PROPERTY, name)

    if action in _UPGRADE_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_UPGRADE, settings.cooldown_property_upgrade
        )
        if not name:
            raise GameError(_USAGE)
        return await properties.upgrade_property(player, name)

    raise GameError(_USAGE)


@router.message(CommandOrText(["property", "estate"], words=PROPERTIES_WORDS))
async def cmd_properties(message: Message, command: CommandObject | None = None) -> None:
    await _reply(message, command)


@router.message(CommandOrText(["crew", "hire", "minion"], words=CREW_WORDS))
async def cmd_crew(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            text, markup = await _render_view(player, properties.KIND_WORKER)
            await message.reply(text, reply_markup=markup)
        elif args[0].casefold() in _BUY_ACTIONS:
            economy.require_ready(
                user.id, properties.ACTION_BUY, settings.cooldown_property_buy
            )
            name = " ".join(args[1:]).strip()
            if not name:
                raise GameError(
                    "👥 نام نیروی مورد نظر را بنویسید.\n"
                    "مثال: <code>نیرو خرید نگهبان شب</code>"
                )
            res = await properties.buy_asset(player, properties.KIND_WORKER, name)
            await message.reply(res["message"])
        else:
            raise GameError(
                "👥 <b>دستورات نوچه و نیرو</b>\n"
                "• <code>نوچه</code> — فهرست نیروهای استخدامی و درآمد انباشته\n"
                "• <code>نوچه خرید [نام]</code> — استخدام نیروی جدید"
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("prop:"))
async def cb_properties_action(call: CallbackQuery) -> None:
    """مدیریت کلیک دکمه‌های شیشه‌ای خرید، ارتقا و وصول درآمد املاک و نوچه‌ها."""
    user = call.from_user
    if user is None or not call.data:
        return

    parts = call.data.split(":")
    act = parts[1] if len(parts) > 1 else "view"
    param = parts[2] if len(parts) > 2 else "property"

    player = await hydrate(user.id, user.full_name, user.username)
    current_kind = properties.KIND_PROPERTY

    def _clean_msg(txt: str) -> str:
        return (
            txt.replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")[:200]
        )

    if act == "view":
        current_kind = (
            properties.KIND_WORKER if param == "worker" else properties.KIND_PROPERTY
        )
        await call.answer()
    elif act == "buy":
        try:
            economy.require_ready(
                user.id, properties.ACTION_BUY, settings.cooldown_property_buy
            )
            res = await properties.buy_asset(player, properties.KIND_PROPERTY, param)
            await call.answer(_clean_msg(res["message"]), show_alert=True)
        except Exception as exc:  # noqa: BLE001
            await call.answer(f"خطا در خرید: {exc}", show_alert=True)
        current_kind = properties.KIND_PROPERTY
    elif act == "buy_w":
        try:
            economy.require_ready(
                user.id, properties.ACTION_BUY, settings.cooldown_property_buy
            )
            res = await properties.buy_asset(player, properties.KIND_WORKER, param)
            await call.answer(_clean_msg(res["message"]), show_alert=True)
        except Exception as exc:  # noqa: BLE001
            await call.answer(f"خطا در استخدام: {exc}", show_alert=True)
        current_kind = properties.KIND_WORKER
    elif act == "upg":
        try:
            economy.require_ready(
                user.id, properties.ACTION_UPGRADE, settings.cooldown_property_upgrade
            )
            res = await properties.upgrade_property(player, param)
            await call.answer(_clean_msg(res["message"]), show_alert=True)
        except Exception as exc:  # noqa: BLE001
            await call.answer(f"خطا در ارتقا: {exc}", show_alert=True)
        current_kind = properties.KIND_PROPERTY
    elif act == "collect":
        try:
            economy.require_ready(
                user.id, properties.ACTION_COLLECT, settings.cooldown_property_collect
            )
            res = await properties.collect(player)
            await call.answer(_clean_msg(res["message"]), show_alert=True)
        except Exception as exc:  # noqa: BLE001
            await call.answer(f"خطا در وصول درآمد: {exc}", show_alert=True)
        current_kind = (
            properties.KIND_WORKER if param == "worker" else properties.KIND_PROPERTY
        )

    fresh = await hydrate(user.id, user.full_name, user.username)
    text, markup = await _render_view(fresh, current_kind)
    msg = editable_message(call)
    if msg:
        with contextlib.suppress(Exception):
            await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
