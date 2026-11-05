"""سیستم رستوران، کافه و تغذیه شهر تیرامیکس.

خرید و مصرف غذاها و نوشیدنی‌های انرژی‌بخش برای بازیابی انرژی کاراکتر جهت کار و فعالیت.
"""

from __future__ import annotations

import time
from typing import Any

from config import FOOD_CATALOG
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy
from services.game import _regen_window


def get_food_catalog() -> tuple[dict[str, Any], ...]:
    """دریافت لیست تمام خوراکی‌ها و نوشیدنی‌های رستوران تیرامیکس."""
    return FOOD_CATALOG


def get_daily_food_menu(day_offset: int = 0) -> list[dict[str, Any]]:
    """انتخاب ۶ غذای متغیر روزانه (از هر سطح ۱ غذا) بر اساس روز تقویمی."""
    day_idx = int((time.time() // 86400) + day_offset)
    menu: list[dict[str, Any]] = []
    for tier in range(1, 7):
        tier_items = [it for it in FOOD_CATALOG if it.get("tier") == tier]
        if tier_items:
            pick = tier_items[(day_idx + tier) % len(tier_items)]
            menu.append(pick)
    return menu


def find_food_item(query: str) -> dict[str, Any] | None:
    """جستجوی غذا با شناسه یا نام فارسی."""
    q = query.strip().lower()
    for item in FOOD_CATALOG:
        if item["id"] == q or q in item["name"].lower():
            return item
    return None


async def buy_and_eat_food(player: Player, food_id_or_name: str) -> dict[str, Any]:
    """خرید و مصرف فوری غذا برای شارژ انرژی شهروند."""
    food = find_food_item(food_id_or_name)
    if not food:
        return {
            "success": False,
            "message": f"❌ غذای «{food_id_or_name}» در منوی رستوران تیرامیکس یافت نشد.",
        }

    price = food["price"]
    creds, _ = await economy.balances(player.user_id)
    if creds < price:
        return {
            "success": False,
            "message": (
                f"❌ موجودی رکسار شما برای سفارش <b>{food['name']}</b> کافی نیست!\n"
                f"💵 قیمت: <b>{price:,}</b> رکسار (موجودی شما: <b>{creds:,}</b> رکسار)"
            ),
        }

    now_ts = int(time.time())
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT energy, energy_updated_at FROM players WHERE user_id = ?",
            (player.user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

    if row is None:
        return {"success": False, "message": "❌ اطلاعات کاراکتر یافت نشد."}

    current_energy, _ = _regen_window(row["energy"], row["energy_updated_at"], now_ts)

    # کسر هزینه غذا
    await economy.spend(player.user_id, credits=price, kind=ActivityKind.SHOP_BUY)

    gained = food["energy"]
    new_energy = current_energy + gained

    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE players
            SET energy = ?,
                energy_updated_at = ?,
                meals_eaten = meals_eaten + 1
            WHERE user_id = ?
            """,
            (new_energy, now_ts, player.user_id),
        )

    energy_status = (
        f"<b>{new_energy}/100</b>"
        if new_energy <= 100
        else f"<b>{new_energy}⚡️</b> (سوپرشارژ 🔥 فراتر از سقف عادی ۱۰۰)"
    )

    return {
        "success": True,
        "food": food,
        "energy_gained": gained,
        "new_energy": new_energy,
        "message": (
            f"😋 <b>نوش جان!</b> شما <b>{food['name']}</b> میل کردید.\n\n"
            f"⚡️ انرژی دریافتی: <b>+{gained}</b>\n"
            f"🔋 سطح انرژی فعلی: {energy_status}\n"
            f"💵 مبلغ پرداختی: <b>{price:,}</b> رکسار\n\n"
            "اکنون با انرژی کامل می‌توانید با دستور <code>کار</code> به شیفت بعدی بروید!"
        ),
    }
