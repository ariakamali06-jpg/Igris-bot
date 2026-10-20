"""Tiramix Beauty Salon, Barber & Solarium service.

Handles:
- Solarium / Paid skin tone transitions (tied to Everskies avatar and players table).
- Comprehensive makeup studio covering hair styles & colors, eyes/lenses/marks,
  nose styles & piercings, ear styles & earrings, lips & lipsticks.
- Economy integration with ActivityKind.SALON and transactional consistency.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from database.connection import db
from models.enums import ActivityKind
from services import avatar, economy

logger = logging.getLogger(__name__)

# Service Fees (Credits)
SKIN_CHANGE_FEE = 500   # سولاریوم و تغییر رنگ پوست (پولی)
HAIR_CHANGE_FEE = 350   # پیرایش، مدل و رنگ مو
EYES_CHANGE_FEE = 300   # لنز رنگی، فرم چشم و خط چشم
NOSE_CHANGE_FEE = 250   # پیرسینگ و فرم بینی
EARS_CHANGE_FEE = 250   # گوشواره و پیرسینگ گوش
LIPS_CHANGE_FEE = 250   # رژ لب و فرم لب

# Skin Tones catalog (Standard slim: -1 to -6, mapped to players.skin_tone)
SKIN_TONES: list[dict[str, Any]] = [
    {"id": -1, "label": "⚪️ خیلی روشن (Fair)", "db_name": "pale"},
    {"id": -2, "label": "🏻 گندمی روشن (Light)", "db_name": "fair"},
    {"id": -3, "label": "🏼 برنزه طبیعی (Natural)", "db_name": "natural"},
    {"id": -4, "label": "🏽 زیتونی / سبزه (Olive)", "db_name": "tan"},
    {"id": -5, "label": "🏾 سبزه تیره (Deep)", "db_name": "dark"},
    {"id": -6, "label": "🏿 تیره / سیاه (Dark)", "db_name": "deep"},
]
SKIN_BY_ID = {item["id"]: item for item in SKIN_TONES}

# Hair catalog (combining items & base models)
HAIR_STYLES: dict[str, str] = {
    "hair1": "🎀 کژوال بلند موج‌دار / فید",
    "hair2": "🌸 لایه‌ای مدرن کره‌ای / ولف‌کات",
    "hair3": "⚡️ دم‌اسبی پرانرژی / فرق بغل",
    "hair4": "💇‍♀️ باب کوتاه شهری / بلند رها",
    "hair5": "✨ شینیون ابریشمی / بوکات",
    "street_fade": "🕶 استریت فید خیابانی",
    "raven_shag": "🦅 کلاغی آشفته و راک",
    "hair_twintails_pink": "🌸 خرگوشی صورتی آیدل",
    "hair_pixie_blue": "💎 پیکسی نئونی آبی",
    "hair_waves_silver": "🌙 آبشار نقره‌ای مانهوا",
}

HAIR_COLORS: dict[str, str] = {
    "black": "🖤 مشکی پرکلاغی",
    "brown": "🌰 قهوه‌ای شکلاتی",
    "blonde": "🌾 بلوند پلاتینیوم",
    "silver": "❄️ نقره‌ای یخی",
    "crimson": "🍷 شرابی سلطنتی",
    "purple": "🔮 بنفش کهکشانی",
    "pink": "🌸 صورتی پاستلی",
    "blue": "💎 آبی الکتریکی",
}

# Eyes catalog
EYE_SHAPES: dict[str, str] = {
    "eyes1_1": "🌸 شاداب و گرد (Sweet Round)",
    "eyes2_1": "⚡️ تیز و جسور (Fierce Sharp)",
    "eyes3_1": "🕊 خونسرد و کشیده (Cool Almond)",
    "eyes4_1": "🔥 بیدارشده سولو لولینگ (Awakened)",
    "arcane_eye_mark": "✨ طرح جادویی زیر چشم (Arcane Mark)",
}

EYE_COLORS: dict[str, str] = {
    "blue": "💎 آبی یاقوتی",
    "green": "🌿 سبز زمردی",
    "amber": "🍯 عسلی درخشان",
    "violet": "🔮 بنفش رویایی",
    "black": "🖤 مشکی پرکلاغی",
    "crimson": "🩸 قرمز یاقوتی",
}

# Nose & Piercing catalog
NOSE_STYLES: dict[str, str] = {
    "nose_natural": "👃 بینی طبیعی کلاسیک",
    "nose_petite": "✨ بینی قلمی عروسکی",
    "nose_diamond_stud": "💎 نگین الماس درخشان بینی",
    "nose_silver_ring": "💍 پیرسینگ حلقه نقره‌ای بینی",
    "nose_double_punk": "⚡️ پیرسینگ دوتایی پانک",
}

# Ears & Earrings catalog
EAR_STYLES: dict[str, str] = {
    "ear_natural": "👂 گوش طبیعی و تمیز",
    "ear_silver_hoops": "⚡️ پیرسینگ حلقه‌ای نقره‌ای",
    "ear_ruby_drop": "🔮 گوشواره آویز یاقوت سلطنتی",
    "ear_industrial_bar": "🖤 پیرسینگ غضروفی اینداستریال",
    "ear_star_cuff": "⭐ ایرکاف ستاره‌ای کریستال",
}

# Lips & Lipstick catalog
LIP_STYLES: dict[str, str] = {
    "lip_natural_nude": "💄 رژ نود طبیعی مات",
    "lip_velvet_cherry": "🍒 رژ مخملی قرمز گیلاسی",
    "lip_royal_wine": "🍷 رژ شرابی اشرافی تیره",
    "lip_peach_gloss": "🍑 رژ شاین هلویی براق",
    "lip_dark_gothic": "🖤 رژ مشکی دارک گوتیک",
}


async def get_player_salon_state(user_id: int) -> dict[str, Any]:
    """Retrieve full appearance and makeup status for salon view."""
    av = await avatar.get_player_avatar(user_id)
    render_url = await avatar.get_player_render_url(user_id)
    credits_bal, _ = await economy.balances(user_id)

    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT skin_tone, eye_style, eye_color, mouth_style,
                   hair_style, hair_color, gender
            FROM players WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

    p_hair = row["hair_style"] if row else "hair1"
    p_hair_col = row["hair_color"] if row else "black"
    p_eye = row["eye_style"] if row else "eyes1_1"
    p_eye_col = row["eye_color"] if row else "amber"
    gender = row["gender"] if row else "نامشخص"

    makeup_data = av.get("makeup_data") or {}
    nose = makeup_data.get("nose_style", "nose_natural")
    ear = makeup_data.get("ear_style", "ear_natural")
    lip = makeup_data.get("lip_style", "lip_natural_nude")

    skin_info = SKIN_BY_ID.get(av["skin_tone"], SKIN_TONES[0])

    return {
        "user_id": user_id,
        "credits": credits_bal,
        "gender": gender,
        "render_url": render_url,
        "skin_tone_id": av["skin_tone"],
        "skin_label": skin_info["label"],
        "hair_style": p_hair,
        "hair_label": HAIR_STYLES.get(p_hair, p_hair),
        "hair_color": p_hair_col,
        "hair_color_label": HAIR_COLORS.get(p_hair_col, p_hair_col),
        "eye_style": p_eye,
        "eye_label": EYE_SHAPES.get(p_eye, p_eye),
        "eye_color": p_eye_col,
        "eye_color_label": EYE_COLORS.get(p_eye_col, p_eye_col),
        "nose_style": nose,
        "nose_label": NOSE_STYLES.get(nose, nose),
        "ear_style": ear,
        "ear_label": EAR_STYLES.get(ear, ear),
        "lip_style": lip,
        "lip_label": LIP_STYLES.get(lip, lip),
    }


async def change_skin_tone_paid(user_id: int, skin_tone_id: int) -> dict[str, Any]:
    """Change skin tone in the salon solarium (costs SKIN_CHANGE_FEE)."""
    if skin_tone_id not in SKIN_BY_ID:
        return {"success": False, "message": "❌ کد رنگ پوست نامعتبر است."}

    skin_def = SKIN_BY_ID[skin_tone_id]
    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < SKIN_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ سکه کافی نداری!\n"
                f"هزینه سولاریوم و تغییر پوست: <b>{SKIN_CHANGE_FEE:,}</b> سکه\n"
                f"موجودی شما: <b>{credits_bal:,}</b> سکه"
            ),
        }

    await economy.spend(user_id, credits=SKIN_CHANGE_FEE, kind=ActivityKind.SALON)

    # Update player_avatar
    await avatar.set_skin_tone(user_id, skin_tone_id)

    # Sync players table skin_tone
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET skin_tone = ? WHERE user_id = ?",
            (skin_def["db_name"], user_id),
        )

    return {
        "success": True,
        "fee": SKIN_CHANGE_FEE,
        "skin_tone_id": skin_tone_id,
        "label": skin_def["label"],
        "message": (
            f"☀️ <b>پوست شما با موفقیت در سولاریوم آرایشگاه برنزه شد!</b>\n"
            f"رنگ جدید: <b>{skin_def['label']}</b>\n"
            f"هزینه پرداختی: <b>{SKIN_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def change_hair_paid(
    user_id: int, style_key: str, color_key: str | None = None
) -> dict[str, Any]:
    """Change hairstyle and color in the barber salon (costs HAIR_CHANGE_FEE)."""
    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < HAIR_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ هزینه پیرایش و رنگ مو <b>{HAIR_CHANGE_FEE:,}</b> سکه است. "
                f"(موجودی: {credits_bal:,})"
            ),
        }

    await economy.spend(user_id, credits=HAIR_CHANGE_FEE, kind=ActivityKind.SALON)

    async with db.write() as conn:
        if color_key:
            await conn.execute(
                "UPDATE players SET hair_style = ?, hair_color = ? WHERE user_id = ?",
                (style_key, color_key, user_id),
            )
        else:
            await conn.execute(
                "UPDATE players SET hair_style = ? WHERE user_id = ?",
                (style_key, user_id),
            )

    # Also record in player_avatar makeup_data
    av = await avatar.get_player_avatar(user_id)
    makeup = av.get("makeup_data") or {}
    makeup["hair_style"] = style_key
    if color_key:
        makeup["hair_color"] = color_key
    await _save_makeup_data(user_id, makeup)

    hair_lbl = HAIR_STYLES.get(style_key, style_key)
    col_lbl = HAIR_COLORS.get(color_key or "", "")
    col_str = f" با رنگ {col_lbl}" if col_lbl else ""

    return {
        "success": True,
        "fee": HAIR_CHANGE_FEE,
        "message": (
            f"✂️ <b>مدل موی جدید با موفقیت کوتاه و اصلاح شد!</b>\n"
            f"استایل: <b>{hair_lbl}</b>{col_str}\n"
            f"هزینه پرداختی: <b>{HAIR_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def change_eyes_paid(
    user_id: int, eye_style: str, eye_color: str | None = None
) -> dict[str, Any]:
    """Change eye shape, marking or lens color (costs EYES_CHANGE_FEE)."""
    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < EYES_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ هزینه میکاپ چشم و لنز <b>{EYES_CHANGE_FEE:,}</b> سکه است. "
                f"(موجودی: {credits_bal:,})"
            ),
        }

    await economy.spend(user_id, credits=EYES_CHANGE_FEE, kind=ActivityKind.SALON)

    async with db.write() as conn:
        if eye_color:
            await conn.execute(
                "UPDATE players SET eye_style = ?, eye_color = ? WHERE user_id = ?",
                (eye_style, eye_color, user_id),
            )
        else:
            await conn.execute(
                "UPDATE players SET eye_style = ? WHERE user_id = ?",
                (eye_style, user_id),
            )

    av = await avatar.get_player_avatar(user_id)
    makeup = av.get("makeup_data") or {}
    makeup["eye_style"] = eye_style
    if eye_color:
        makeup["eye_color"] = eye_color
    await _save_makeup_data(user_id, makeup)

    e_lbl = EYE_SHAPES.get(eye_style, eye_style)
    c_lbl = EYE_COLORS.get(eye_color or "", "")
    c_str = f" و لنز {c_lbl}" if c_lbl else ""

    return {
        "success": True,
        "fee": EYES_CHANGE_FEE,
        "message": (
            f"👁 <b>میکاپ چشم و لنز با موفقیت اعمال شد!</b>\n"
            f"فرم چشم: <b>{e_lbl}</b>{c_str}\n"
            f"هزینه پرداختی: <b>{EYES_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def change_nose_paid(user_id: int, nose_style: str) -> dict[str, Any]:
    """Change nose shape or piercing (costs NOSE_CHANGE_FEE)."""
    if nose_style not in NOSE_STYLES:
        return {"success": False, "message": "❌ مدل بینی نامعتبر است."}

    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < NOSE_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ هزینه پیرسینگ و فرم بینی <b>{NOSE_CHANGE_FEE:,}</b> سکه است. "
                f"(موجودی: {credits_bal:,})"
            ),
        }

    await economy.spend(user_id, credits=NOSE_CHANGE_FEE, kind=ActivityKind.SALON)

    av = await avatar.get_player_avatar(user_id)
    makeup = av.get("makeup_data") or {}
    makeup["nose_style"] = nose_style
    await _save_makeup_data(user_id, makeup)

    lbl = NOSE_STYLES[nose_style]
    return {
        "success": True,
        "fee": NOSE_CHANGE_FEE,
        "message": (
            f"👃 <b>فرم و پیرسینگ بینی با موفقیت ثبت شد!</b>\n"
            f"استایل: <b>{lbl}</b>\n"
            f"هزینه پرداختی: <b>{NOSE_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def change_ears_paid(user_id: int, ear_style: str) -> dict[str, Any]:
    """Change ear piercings or earrings (costs EARS_CHANGE_FEE)."""
    if ear_style not in EAR_STYLES:
        return {"success": False, "message": "❌ مدل گوشواره/پیرسینگ نامعتبر است."}

    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < EARS_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ هزینه گوشواره و پیرسینگ گوش <b>{EARS_CHANGE_FEE:,}</b> سکه است. "
                f"(موجودی: {credits_bal:,})"
            ),
        }

    await economy.spend(user_id, credits=EARS_CHANGE_FEE, kind=ActivityKind.SALON)

    av = await avatar.get_player_avatar(user_id)
    makeup = av.get("makeup_data") or {}
    makeup["ear_style"] = ear_style
    await _save_makeup_data(user_id, makeup)

    lbl = EAR_STYLES[ear_style]
    return {
        "success": True,
        "fee": EARS_CHANGE_FEE,
        "message": (
            f"👂 <b>پیرسینگ و گوشواره گوش با موفقیت آویخته شد!</b>\n"
            f"استایل: <b>{lbl}</b>\n"
            f"هزینه پرداختی: <b>{EARS_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def change_lips_paid(user_id: int, lip_style: str) -> dict[str, Any]:
    """Change lips makeup and lipstick (costs LIPS_CHANGE_FEE)."""
    if lip_style not in LIP_STYLES:
        return {"success": False, "message": "❌ مدل رژ لب نامعتبر است."}

    credits_bal, _ = await economy.balances(user_id)
    if credits_bal < LIPS_CHANGE_FEE:
        return {
            "success": False,
            "message": (
                f"❌ هزینه رژ لب و میکاپ لب <b>{LIPS_CHANGE_FEE:,}</b> سکه است. "
                f"(موجودی: {credits_bal:,})"
            ),
        }

    await economy.spend(user_id, credits=LIPS_CHANGE_FEE, kind=ActivityKind.SALON)

    av = await avatar.get_player_avatar(user_id)
    makeup = av.get("makeup_data") or {}
    makeup["lip_style"] = lip_style
    await _save_makeup_data(user_id, makeup)

    lbl = LIP_STYLES[lip_style]
    return {
        "success": True,
        "fee": LIPS_CHANGE_FEE,
        "message": (
            f"👄 <b>رژ لب و میکاپ صورت با موفقیت اعمال شد!</b>\n"
            f"استایل: <b>{lbl}</b>\n"
            f"هزینه پرداختی: <b>{LIPS_CHANGE_FEE:,}</b> سکه"
        ),
    }


async def _save_makeup_data(user_id: int, makeup_data: dict[str, Any]) -> None:
    """Save serialized makeup json into player_avatar."""
    now_ts = int(time.time())
    raw = json.dumps(makeup_data)
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE player_avatar
            SET makeup_data = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (raw, now_ts, user_id),
        )
