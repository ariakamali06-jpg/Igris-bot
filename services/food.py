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
    if current_energy >= 100:
        return {
            "success": False,
            "message": "⚡️ <b>شکمت کاملاً سیره و پر از انرژی هستی!</b> (انرژی: ۱۰۰/۱۰۰)\n"
                       "نیازی به غذا خوردن نداری؛ برو سراغ شیفت کاری با دستور <code>کار</code>!",
        }

    # کسر هزینه غذا
    await economy.spend(player.user_id, credits=price, kind=ActivityKind.SHOP_BUY)

    gained = food["energy"]
    new_energy = min(100, current_energy + gained)

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET energy = ?, energy_updated_at = ? WHERE user_id = ?",
            (new_energy, now_ts, player.user_id),
        )

    return {
        "success": True,
        "food": food,
        "energy_gained": gained,
        "new_energy": new_energy,
        "message": (
            f"😋 <b>نوش جان!</b> شما <b>{food['name']}</b> میل کردید.\n\n"
            f"⚡️ انرژی دریافتی: <b>+{gained}</b>\n"
            f"🔋 سطح انرژی فعلی: <b>{new_energy}/100</b>\n"
            f"💵 مبلغ پرداختی: <b>{price:,}</b> رکسار\n\n"
            "اکنون با انرژی کامل می‌توانید با دستور <code>کار</code> به شیفت بعدی بروید!"
        ),
    }
