"""تست‌های جامع ارتقای هویت و امکانات v3 ربات تیرامیکس:
- تب‌های دسته‌بندی پروفایل
- گسترش املاک و دکمه‌های شیشه‌ای
- نام‌های مستعار دستورات (کاپ، نوچه، پت، بازار سیاه)
- تفکیک جنسیت در آرایشگاه (حذف رژ لب برای آقایان و مدل موی پسرانه)
- سطح‌بندی سواد با ترم‌های تحصیلی
- ارتقای لول با دفعات کارکردن
- سیستم رستوران و کالیبراسیون ۱۲ کار با ۳ وعده غذا
"""

from __future__ import annotations

import time

import pytest

from config import FOOD_CATALOG, PROPERTY_CATALOG, WORKER_CATALOG
from database.connection import db
from database.seed import seed_catalog
from handlers import blackmarket, cup, pets, profile, properties, security
from handlers import salon as salon_h
from models.enums import ActivityKind
from services import economy, food, game, tiramix


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_profile_tabs_and_category_markup():
    p = await game.ensure_player(777001, "Salar", "salar")
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET onboarding_completed = 1 WHERE user_id = 777001"
        )

    # 1. Main summary
    cap_main, _ = await profile._profile_panel(p, active_tab="main")
    assert "شناسنامه شهروندی" in cap_main
    assert "انرژی" in cap_main

    # 2. Stats tab
    cap_stats, _ = await profile._profile_panel(p, active_tab="stats")
    assert "آمار مبارزه" in cap_stats
    assert "قدرت تهاجمی" in cap_stats
    assert "دفاع و استقامت" in cap_stats
    assert "امتیاز استایل" in cap_stats

    # 3. Job tab
    cap_job, _ = await profile._profile_panel(p, active_tab="job")
    assert "وضعیت شغلی و پیشرفت تحصیلی" in cap_job
    assert "شیفت‌های کاری" in cap_job

    # 4. Assets tab
    cap_assets, _ = await profile._profile_panel(p, active_tab="assets")
    assert "املاک، دارایی‌ها" in cap_assets
    assert "درآمد منفعل" in cap_assets

    # 5. Social tab
    cap_social, _ = await profile._profile_panel(p, active_tab="social")
    assert "زندگی اجتماعی" in cap_social

    # Markup check
    markup = await profile._profile_markup("main")
    btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("مشخصات" in t for t in btn_texts)
    assert any("شغل و تحصیل" in t for t in btn_texts)
    assert any("املاک و دارایی" in t for t in btn_texts)
    assert any("زندگی و کلن" in t for t in btn_texts)


@pytest.mark.asyncio
async def test_real_estate_expanded_catalog_and_buttons():
    # Verify expanded catalog sizes and higher scales
    assert len(PROPERTY_CATALOG) >= 10
    assert len(WORKER_CATALOG) >= 6
    assert any(p["name"] == "پنت‌هاوس سلطنتی" for p in PROPERTY_CATALOG)
    assert any(w["name"] == "تک‌تیرانداز مافیا" for w in WORKER_CATALOG)

    await game.ensure_player(777002, "Realtor", "realtor")
    owned = [{"name": "کارگاه زیرزمینی", "level": 1, "last_collect": int(time.time())}]
    markup_p = properties._property_markup(owned)
    btn_texts_p = [btn.text for row in markup_p.inline_keyboard for btn in row]
    assert any("خرید دکه روزنامه‌فروشی" in t for t in btn_texts_p)
    assert any("ارتقای کارگاه زیرزمینی" in t for t in btn_texts_p)
    assert any("وصول درآمد" in t for t in btn_texts_p)

    markup_w = properties._worker_markup([])
    btn_texts_w = [btn.text for row in markup_w.inline_keyboard for btn in row]
    assert any("استخدام نوچه خیابانی" in t for t in btn_texts_w)


@pytest.mark.asyncio
async def test_missing_command_aliases():
    # کاپ
    assert "کاپ" in cup.CUP_WORDS
    assert "تورنمنت" in cup.CUP_WORDS

    # نوچه
    assert "نوچه" in properties.CREW_WORDS
    assert "بادیگارد" in properties.CREW_WORDS

    # پت
    assert "پت" in pets.PET_WORDS

    # بازار سیاه
    assert "بازار سیاه" in blackmarket.BLACKMARKET_WORDS

    # سرقت بانک
    assert "سرقت بانک" in security.ROBBERY_WORDS


@pytest.mark.asyncio
async def test_salon_gender_partitioning():
    # Male character
    await game.ensure_player(777003, "BoyPlayer", "boy_player")
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET gender = 'پسر', onboarding_completed = 1 WHERE user_id = 777003"
        )

    _, cap_m, mark_m = await salon_h._build_salon_menu(777003)
    assert "پیرایشگاه آقایان" in cap_m
    assert "رژ لب" not in cap_m
    m_btns = [btn.text for row in mark_m.inline_keyboard for btn in row]
    assert not any("رژ" in t for t in m_btns)

    _, _, hair_m = await salon_h._build_hair_menu(777003)
    hair_btns_m = [btn.text for row in hair_m.inline_keyboard for btn in row]
    assert any("فید" in t or "ای‌بوی" in t or "اسپایکی" in t for t in hair_btns_m)
    assert not any("شینیون" in t or "خرگوشی" in t for t in hair_btns_m)

    # Female character
    await game.ensure_player(777004, "GirlPlayer", "girl_player")
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET gender = 'دختر', onboarding_completed = 1 WHERE user_id = 777004"
        )

    _, cap_f, mark_f = await salon_h._build_salon_menu(777004)
    assert "بانوان" in cap_f
    assert "رژ لب" in cap_f
    f_btns = [btn.text for row in mark_f.inline_keyboard for btn in row]
    assert any("رژ" in t for t in f_btns)


@pytest.mark.asyncio
async def test_education_step_progression():
    p_init = await game.ensure_player(777005, "Student", "student")
    await economy.grant(p_init.user_id, credits=50000, kind=ActivityKind.ADMIN)

    # Diploma needs 3 steps (terms)
    p = await game.load_player(p_init.user_id)
    assert p is not None
    assert p.education_level == 0
    assert p.education_step == 0

    # Step 1
    res1 = await tiramix.execute_study(p)
    assert res1["success"] is True
    assert res1["new_step"] == 1
    assert res1["new_level"] == 0

    # Step 2
    p = await game.load_player(p_init.user_id)
    assert p is not None
    p.last_study_time = 0
    res2 = await tiramix.execute_study(p)
    assert res2["success"] is True
    assert res2["new_step"] == 2
    assert res2["new_level"] == 0

    # Step 3 -> Graduation!
    p = await game.load_player(p_init.user_id)
    assert p is not None
    p.last_study_time = 0
    res3 = await tiramix.execute_study(p)
    assert res3["success"] is True
    assert res3["new_step"] == 0
    assert res3["new_level"] == 1
    assert "دیپلم" in res3["new_title"]


@pytest.mark.asyncio
async def test_work_count_leveling_and_energy_calibration():
    p_init = await game.ensure_player(777006, "Worker", "worker")
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET onboarding_completed = 1, energy = 100 WHERE user_id = 777006"
        )

    # 4 works consume 4 * 25 = 100 energy
    for _i in range(4):
        p = await game.load_player(p_init.user_id)
        assert p is not None
        p.last_work_time = 0
        res = await tiramix.execute_work(p)
        assert res["success"] is True

    # 5th work must fail due to zero energy
    p = await game.load_player(p_init.user_id)
    assert p is not None
    p.last_work_time = 0
    res_fail = await tiramix.execute_work(p)
    assert res_fail["success"] is False
    assert res_fail["error"] == "energy"

    # Meal 1: Pizza (+85 energy)
    await economy.grant(p_init.user_id, credits=10000, kind=ActivityKind.ADMIN)
    food_res = await food.buy_and_eat_food(p, "pizza")
    assert food_res["success"] is True
    assert food_res["new_energy"] >= 85

    # Player can work again!
    p = await game.load_player(p_init.user_id)
    assert p is not None
    p.last_work_time = 0
    res_again = await tiramix.execute_work(p)
    assert res_again["success"] is True

    # Test work milestone leveling (10 works -> Level 2)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET work_count = 9, level = 1, energy = 100 WHERE user_id = 777006"
        )
    p = await game.load_player(p_init.user_id)
    assert p is not None
    p.last_work_time = 0
    res_lvl2 = await tiramix.execute_work(p)
    assert res_lvl2["level_up"] is True
    assert res_lvl2["new_level"] == 2
    fresh = await game.load_player(p_init.user_id)
    assert fresh is not None
    assert fresh.level == 2


@pytest.mark.asyncio
async def test_restaurant_menu_and_eating():
    assert len(FOOD_CATALOG) >= 6
    p_init = await game.ensure_player(777007, "Hungry", "hungry")
    await economy.grant(p_init.user_id, credits=5000, kind=ActivityKind.ADMIN)
    async with db.write() as conn:
        await conn.execute("UPDATE players SET energy = 30 WHERE user_id = 777007")

    p = await game.load_player(p_init.user_id)
    assert p is not None
    res_eat = await food.buy_and_eat_food(p, "burger")
    assert res_eat["success"] is True
    assert res_eat["energy_gained"] == 75
    assert res_eat["new_energy"] == 105

    # Eating when already >= 100 energy is allowed and stacks up further (uncapped via food)
    p = await game.load_player(p_init.user_id)
    assert p is not None
    res_boost = await food.buy_and_eat_food(p, "espresso")
    assert res_boost["success"] is True
    assert res_boost["energy_gained"] == 25
    assert res_boost["new_energy"] == 130
