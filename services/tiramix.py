"""Tiramix Life Simulator Service.

Encapsulates economy, education, career, banking, criminal justice,
relationships, and clan mechanics for the City of Tiramix.
"""

from __future__ import annotations

import random
import time
from typing import Any

from config import settings
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy

# ---------------------------------------------------------------------------
# Constants & Rules
# ---------------------------------------------------------------------------

WORK_COOLDOWN = 7200        # 2 hours
STUDY_COOLDOWN = 14400      # 4 hours
STEAL_COOLDOWN = 21600      # 6 hours
DUEL_COOLDOWN = 10800       # 3 hours
INTIMACY_COOLDOWN = 28800   # 8 hours
AFFAIR_COOLDOWN = 43200     # 12 hours
JAIL_DURATION = 1800        # 30 minutes
PREGNANCY_DURATION = 86400  # 24 hours

TUITION_FEES = {
    0: 500,       # 0 -> 1: Diploma
    1: 2500,      # 1 -> 2: Bachelor
    2: 8000,      # 2 -> 3: Master
    3: 25000,     # 3 -> 4: PhD
    4: 70000,     # 4 -> 5: Professor/Genius
}

JOBS_CATALOG: dict[str, dict[str, Any]] = {
    "کارگر ساختمانی": {"level": 0, "wage": 80, "desc": "کار در پروژه‌های عمرانی تیرامیکس"},
    "نظافتچی شهری": {"level": 0, "wage": 70, "desc": "پاکسازی پارک‌ها و پیاده‌روها"},
    "پیک موتوری": {"level": 0, "wage": 90, "desc": "تحویل سریع بسته‌های پستی"},
    "صندوقدار هایپرمارکت": {"level": 1, "wage": 160, "desc": "مدیریت صندوق فروشگاه مرکزی"},
    "راننده تاکسی": {"level": 1, "wage": 190, "desc": "جابجایی مسافران در خطوط شهری"},
    "نگهبان برج": {"level": 1, "wage": 170, "desc": "حفاظت از برج‌های مدرن تیرامیکس"},
    "حسابدار شرکت": {"level": 2, "wage": 380, "desc": "تنظیم دفاتر مالی و ترازنامه"},
    "برنامه‌نویس جونیور": {"level": 2, "wage": 450, "desc": "توسعه نرم‌افزار و رفع باگ"},
    "معلم دبیرستان": {"level": 2, "wage": 350, "desc": "تدریس دروس پایه به نوجوانان"},
    "مدیر پروژه": {"level": 3, "wage": 800, "desc": "هدایت تیم‌های چندمنظوره"},
    "تحلیلگر ارشد بورس": {"level": 3, "wage": 950, "desc": "پیش‌بینی روندهای بازار مالی"},
    "معمار ارشد": {"level": 3, "wage": 880, "desc": "طراحی آسمان‌خراش‌های تیرامیکس"},
    "جراح کلینیک": {"level": 4, "wage": 1700, "desc": "انجام عمل‌های پیچیده و نجات جان بیماران"},
    "استاد دانشگاه": {"level": 4, "wage": 1600, "desc": "تحقیقات علمی و آموزش دانشگاهی"},
    "وکیل ارشد دادگستری": {"level": 4, "wage": 1900, "desc": "وکالت در پرونده‌های کلان و دعاوی"},
    "رئیس بانک مرکزی": {"level": 5, "wage": 3800, "desc": "تنظیم سیاست‌های پولی شهر"},
    "سرمایه‌گذار کلان": {"level": 5, "wage": 5500, "desc": "مدیریت صندوق‌های خطرپذیر و سهام"},
}

HAIRSTYLE_FEES = 350
SURGERY_FEES = 850
CLINIC_HEAL_FEE = 400
DELIVERY_FEE = 1200


# ---------------------------------------------------------------------------
# Work & Career
# ---------------------------------------------------------------------------

async def execute_work(player: Player) -> dict[str, Any]:
    """Execute citizen work shift."""
    now = int(time.time())
    if player.is_in_jail(now):
        remaining = int((player.is_jailed_until - now) // 60)
        return {
            "success": False,
            "error": "jail",
            "message": f"🚨 <b>وَخَه بینُم خلافکار!</b> تو در زندان تیرامیکس حبس شدی و تا {remaining} دقیقه دیگه حق کار کردن نداری!",
        }

    elapsed = now - player.last_work_time
    if elapsed < WORK_COOLDOWN:
        rem_min = int((WORK_COOLDOWN - elapsed) // 60)
        rem_sec = int((WORK_COOLDOWN - elapsed) % 60)
        return {
            "success": False,
            "error": "cooldown",
            "message": f"⏳ شما به تازگی شیفت کاری خود را به پایان رسانده‌اید.\nاستراحت لازم: <b>{rem_min} دقیقه و {rem_sec} ثانیه</b> دیگر.",
        }

    job_info = JOBS_CATALOG.get(player.job)
    base_wage = job_info["wage"] if job_info else 50
    # Bonus based on education and level
    bonus = int(player.level * 8 + player.education_level * 25)
    total_earned = base_wage + bonus

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET last_work_time = ? WHERE user_id = ?",
            (now, player.user_id),
        )

    await economy.grant(player.user_id, credits=total_earned, kind=ActivityKind.WORK)

    return {
        "success": True,
        "job": player.job,
        "base_wage": base_wage,
        "bonus": bonus,
        "total_earned": total_earned,
        "message": f"💼 <b>پایان شیفت کاری موفقیت‌آمیز!</b>\n\n"
                   f"📌 شغل: <b>{player.job}</b>\n"
                   f"💵 دستمزد پایه: <b>{base_wage:,}</b> سکه\n"
                   f"✨ پاداش تخصص و لول: <b>{bonus:,}</b> سکه\n"
                   f"💰 مجموع واریزی به کیف پول: <b>+{total_earned:,}</b> سکه تیرامیکس",
    }


async def execute_study(player: Player) -> dict[str, Any]:
    """Enroll in school / university to increase education level."""
    now = int(time.time())
    if player.is_in_jail(now):
        remaining = int((player.is_jailed_until - now) // 60)
        return {
            "success": False,
            "error": "jail",
            "message": f"🚨 <b>وَخَه بینُم خلافکار!</b> پشت میله‌های زندان دانشگاه وجود ندارد! تا {remaining} دقیقه دیگر آزاد می‌شوی.",
        }

    current_edu = player.education_level
    if current_edu >= 5:
        return {
            "success": False,
            "error": "max_level",
            "message": "🎓 شما به بالاترین درجه علمی تیرامیکس (پروفسور و نابغه شهری) دست یافته‌اید!",
        }

    tuition = TUITION_FEES[current_edu]
    wallet_creds, _ = await economy.balances(player.user_id)
    if wallet_creds < tuition:
        return {
            "success": False,
            "error": "funds",
            "message": f"❌ موجودی کیف پول شما کافی نیست!\nشهریه ترم جدید: <b>{tuition:,}</b> سکه (موجودی شما: <b>{wallet_creds:,}</b> سکه)",
        }

    elapsed = now - player.last_study_time
    if elapsed < STUDY_COOLDOWN:
        rem_min = int((STUDY_COOLDOWN - elapsed) // 60)
        return {
            "success": False,
            "error": "cooldown",
            "message": f"⏳ مغز شما نیاز به استراحت دارد! کلاس بعدی پس از <b>{rem_min} دقیقه</b> دیگر آغاز می‌شود.",
        }

    await economy.spend(player.user_id, credits=tuition, kind=ActivityKind.STUDY)

    new_edu = current_edu + 1
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET education_level = ?, last_study_time = ? WHERE user_id = ?",
            (new_edu, now, player.user_id),
        )

    titles = {
        1: "دیپلم متوسطه 📜",
        2: "کارشناسی (لیسانس) 🎓",
        3: "کارشناسی ارشد (فوق لیسانس) 🏛",
        4: "دکترا (PhD) 🔬",
        5: "پروفسور و نابغه شهری 👑",
    }
    new_title = titles.get(new_edu, "نامشخص")

    return {
        "success": True,
        "old_level": current_edu,
        "new_level": new_edu,
        "new_title": new_title,
        "tuition": tuition,
        "message": f"🎉 <b>فارغ‌التحصیلی و ارتقای سطح سواد!</b>\n\n"
                   f"شهریه پرداختی: <b>{tuition:,}</b> سکه\n"
                   f"مدرک جدید شما: <b>{new_title}</b>\n\n"
                   f"اکنون می‌توانید با دستور <code>شغل</code> به فرصت‌های شغلی با درآمد بالاتر دسترسی پیدا کنید!",
    }


def list_available_jobs(education_level: int) -> list[dict[str, Any]]:
    """List jobs unlocked for a given education tier."""
    jobs: list[dict[str, Any]] = []
    for title, details in JOBS_CATALOG.items():
        if details["level"] <= education_level:
            jobs.append({
                "title": title,
                "wage": details["wage"],
                "required_level": details["level"],
                "desc": details["desc"],
            })
    return jobs


async def set_player_job(player: Player, job_title: str) -> dict[str, Any]:
    """Switch career if qualified."""
    if job_title not in JOBS_CATALOG:
        return {"success": False, "message": f"❌ شغل «{job_title}» در سازمان استخدامی تیرامیکس یافت نشد."}

    req_level = JOBS_CATALOG[job_title]["level"]
    if player.education_level < req_level:
        return {
            "success": False,
            "message": f"⛔️ مدرک تحصیلی شما برای این شغل کافی نیست!\nنیاز به مدرک حداقل سطح {req_level} دارید. با دستور <code>تحصیل</code> مدرک خود را ارتقا دهید.",
        }

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET job = ? WHERE user_id = ?",
            (job_title, player.user_id),
        )

    return {
        "success": True,
        "job": job_title,
        "wage": JOBS_CATALOG[job_title]["wage"],
        "message": f"✅ تبریک! قرارداد کاری شما به عنوان <b>{job_title}</b> امضا شد.\nدستمزد پایه ساعتی: <b>{JOBS_CATALOG[job_title]['wage']:,}</b> سکه.",
    }


# ---------------------------------------------------------------------------
# Bank & Loans
# ---------------------------------------------------------------------------

async def deposit_bank(player: Player, amount: int) -> dict[str, Any]:
    """Deposit cash into secure bank vault (immune to theft)."""
    if amount <= 0:
        return {"success": False, "message": "❌ مبلغ واریز باید بیشتر از صفر باشد."}

    creds, _ = await economy.balances(player.user_id)
    if creds < amount:
        return {"success": False, "message": f"❌ موجودی نقد شما کافی نیست! (موجودی: {creds:,} سکه)"}

    await economy.spend(player.user_id, credits=amount, kind=ActivityKind.BANK_DEPOSIT)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET bank_balance = bank_balance + ? WHERE user_id = ?",
            (amount, player.user_id),
        )

    new_bal = player.bank_balance + amount
    return {
        "success": True,
        "amount": amount,
        "bank_balance": new_bal,
        "message": f"🏦 <b>واریز امن به بانک مرکزی تیرامیکس</b>\n\n"
                   f"مبلغ: <b>{amount:,}</b> سکه با موفقیت در گاوصندوق ضدسرقت ذخیره شد.\n"
                   f"موجودی جدید حساب بانکی: <b>{new_bal:,}</b> سکه",
    }


async def withdraw_bank(player: Player, amount: int) -> dict[str, Any]:
    """Withdraw money from bank vault to cash wallet."""
    if amount <= 0:
        return {"success": False, "message": "❌ مبلغ برداشت باید بیشتر از صفر باشد."}

    if player.bank_balance < amount:
        return {"success": False, "message": f"❌ موجودی حساب بانکی شما کافی نیست! (موجودی: {player.bank_balance:,} سکه)"}

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET bank_balance = bank_balance - ? WHERE user_id = ?",
            (amount, player.user_id),
        )

    await economy.grant(player.user_id, credits=amount, kind=ActivityKind.BANK_WITHDRAW)
    new_bal = player.bank_balance - amount
    return {
        "success": True,
        "amount": amount,
        "bank_balance": new_bal,
        "message": f"🏧 <b>برداشت وجه از بانک</b>\n\n"
                   f"مبلغ: <b>{amount:,}</b> سکه به کیف پول نقد شما منتقل شد.\n"
                   f"موجودی باقی‌مانده در بانک: <b>{new_bal:,}</b> سکه",
    }


async def apply_bank_loan(player: Player, requested_amount: int) -> dict[str, Any]:
    """Apply for a bank loan based on collateral and credit score."""
    if player.loan_amount > 0:
        return {
            "success": False,
            "message": f"❌ شما هم‌اکنون دارای یک فقره وام تسویه‌نشده به مبلغ <b>{player.loan_amount:,}</b> سکه هستید!\nابتدا بدهی خود را پرداخت کنید.",
        }

    # Credit limit = base + balance * 2 + level * 500
    credit_limit = 1000 + (player.bank_balance * 2) + (player.level * 500)
    if requested_amount <= 0 or requested_amount > credit_limit:
        return {
            "success": False,
            "message": f"❌ سقف مجاز تسهیلات برای شما حداکثر <b>{credit_limit:,}</b> سکه است.\n(محاسبه بر اساس موجودی بانکی و اعتبار شهروندی)",
        }

    interest_rate = 0.15  # 15% interest
    total_due = int(requested_amount * (1 + interest_rate))
    due_date = int(time.time()) + (3 * 86400)  # 3 days repayment window

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET loan_amount = ?, loan_due = ? WHERE user_id = ?",
            (total_due, due_date, player.user_id),
        )

    await economy.grant(player.user_id, credits=requested_amount, kind=ActivityKind.LOAN)

    return {
        "success": True,
        "amount": requested_amount,
        "total_due": total_due,
        "interest": int(requested_amount * interest_rate),
        "message": f"🏦 <b>تسهیلات بانکی تایید و واریز شد!</b>\n\n"
                   f"مبلغ وام دریافتی: <b>+{requested_amount:,}</b> سکه\n"
                   f"بهره بانکی (۱۵٪): <b>{int(requested_amount * interest_rate):,}</b> سکه\n"
                   f"کل مبلغ بازپرداخت: <b>{total_due:,}</b> سکه\n"
                   f"مهلت تسویه: ۳ روز کاری\n\n"
                   f"برای تسویه حساب از دستور <code>بانک تسویه</code> استفاده کنید.",
    }


async def repay_bank_loan(player: Player, amount: int) -> dict[str, Any]:
    """Repay part or all of the outstanding loan."""
    if player.loan_amount <= 0:
        return {"success": False, "message": "✅ شما هیچ بدهی بانکی فعال ندارید."}

    to_pay = min(amount, player.loan_amount)
    creds, _ = await economy.balances(player.user_id)
    if creds < to_pay:
        return {"success": False, "message": f"❌ موجودی کیف پول برای پرداخت اقساط کافی نیست! (نیاز: {to_pay:,} سکه)"}

    await economy.spend(player.user_id, credits=to_pay, kind=ActivityKind.LOAN)
    new_due = player.loan_amount - to_pay

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET loan_amount = ? WHERE user_id = ?",
            (new_due, player.user_id),
        )

    return {
        "success": True,
        "paid": to_pay,
        "remaining_due": new_due,
        "message": f"💳 <b>تسویه اقساط بانکی</b>\n\n"
                   f"مبلغ پرداختی: <b>{to_pay:,}</b> سکه\n"
                   f"بدهی باقی‌مانده: <b>{new_due:,}</b> سکه",
    }


# ---------------------------------------------------------------------------
# Crime: Theft, Jail & Penalties
# ---------------------------------------------------------------------------

async def attempt_theft(thief: Player, victim: Player) -> dict[str, Any]:
    """Attempt pickpocketing/burglary against another citizen in group."""
    now = int(time.time())
    if thief.user_id == victim.user_id:
        return {"success": False, "message": "❌ نمی‌توانید از خودتان دزدی کنید!"}

    if thief.is_in_jail(now):
        rem = int((thief.is_jailed_until - now) // 60)
        return {
            "success": False,
            "message": f"🚨 <b>وَخَه بینُم خلافکار!</b> تو در زندان مرکزی حبس هستی و دستت به جیب کسی نمی‌رسه! تا {rem} دقیقه دیگر در بند هستی.",
        }

    if victim.is_in_jail(now):
        return {"success": False, "message": "❌ این شهروند در زندان به سر می‌برد و قابل سرقت نیست!"}

    elapsed = now - thief.last_steal_time
    if elapsed < STEAL_COOLDOWN:
        rem_min = int((STEAL_COOLDOWN - elapsed) // 60)
        return {
            "success": False,
            "message": f"⏳ پلیس شهر تیرامیکس به شما مشکوک است! برای سرقت بعدی باید <b>{rem_min} دقیقه</b> صبر کنید.",
        }

    # سپر ضدسرقت: the victim's active cover turns the attempt into a clean
    # miss — no loot, no jail, no fine for the thief (Ocean port phase 1).
    if victim.shield_until > now:
        return {
            "success": False,
            "blocked": "shield",
            "message": f"🛡️ <b>سپر ضدسرقت {victim.display_name} کار کرد!</b>\n\n"
                       "دستت به جیب نرسید و بی‌سر و صدا عقب کشیدی؛ نه زندان، نه جریمه.",
        }

    victim_cash, _ = await economy.balances(victim.user_id)
    if victim_cash < 50:
        return {
            "success": False,
            "message": f"❌ جیب {victim.display_name} خالی‌تر از آن است که ارزش ریسک داشته باشد! (زیر ۵۰ سکه)",
        }

    # Theft percentage: 35% to 85% of victim's cash
    steal_pct = random.uniform(0.35, 0.85)
    target_loot = max(10, int(victim_cash * steal_pct))

    # Success probability: base 45% + level diff * 3% + education diff * 4%
    diff_lvl = thief.level - victim.level
    diff_edu = thief.education_level - victim.education_level
    chance = 0.45 + (diff_lvl * 0.03) + (diff_edu * 0.04)
    chance = max(0.15, min(0.85, chance))

    roll = random.random()
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET last_steal_time = ? WHERE user_id = ?",
            (now, thief.user_id),
        )

    if roll <= chance:
        # Success! Loot transferred
        await economy.spend(victim.user_id, credits=target_loot, kind=ActivityKind.THEFT)
        await economy.grant(thief.user_id, credits=target_loot, kind=ActivityKind.THEFT)
        return {
            "success": True,
            "stolen": target_loot,
            "percentage": int(steal_pct * 100),
            "message": f"🥷 <b>سرقت موفقیت‌آمیز در کوچه پس‌کوچه‌های تیرامیکس!</b>\n\n"
                       f"دزد حرفه‌ای ({thief.display_name}) موفق شد <b>{target_loot:,}</b> سکه "
                       f"({int(steal_pct * 100)}٪ از دارایی نقد) از جیب {victim.display_name} به جیب بزند!",
        }
    else:
        # Caught by police!
        # Penalty: 5x the intended loot amount or jail time!
        fine_amount = target_loot * 5
        thief_cash, _ = await economy.balances(thief.user_id)

        if thief_cash >= fine_amount:
            # Pay 5x fine to victim & court
            await economy.spend(thief.user_id, credits=fine_amount, kind=ActivityKind.THEFT)
            # 50% of fine to victim compensation, 50% to city court
            compensation = fine_amount // 2
            await economy.grant(victim.user_id, credits=compensation, kind=ActivityKind.THEFT)

            return {
                "success": False,
                "punishment": "fine",
                "fine": fine_amount,
                "compensation": compensation,
                "message": f"🚨 <b>دستگیری دزد ناشی توسط پلیس تیرامیکس!</b>\n\n"
                           f"سارق ({thief.display_name}) حین اقدام به سرقت از {victim.display_name} به دام افتاد!\n"
                           f"⚖️ حکم دادگاه: پرداخت <b>۵ برابر مبلغ سرقت</b> معادل <b>{fine_amount:,}</b> سکه جریمه نقدی.\n"
                           f"💵 مبلغ <b>{compensation:,}</b> سکه به عنوان غرامت به جیب مال‌باخته واریز شد!",
            }
        else:
            # Cannot pay full fine -> Jailed for 30 minutes!
            jail_until = now + JAIL_DURATION
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET is_jailed_until = ? WHERE user_id = ?",
                    (jail_until, thief.user_id),
                )
            # Confiscate all remaining cash as bail
            if thief_cash > 0:
                await economy.spend(thief.user_id, credits=thief_cash, kind=ActivityKind.THEFT)
                await economy.grant(victim.user_id, credits=thief_cash, kind=ActivityKind.THEFT)

            return {
                "success": False,
                "punishment": "jail",
                "jail_minutes": int(JAIL_DURATION // 60),
                "message": f"🚨 <b>دستگیری و محکومیت به حبس تعزیری!</b>\n\n"
                           f"سارق ({thief.display_name}) توانایی پرداخت جریمه ۵ برابری را نداشت!\n"
                           f"🔒 به دستور قاضی، او به مدت <b>۳۰ دقیقه</b> به بازداشتگاه مرکزی تیرامیکس منتقل شد.\n"
                           f"⚠️ در طول مدت حبس هیچ دستوری برای این بازیکن کار نخواهد کرد!",
            }


# ---------------------------------------------------------------------------
# Social: Marriage, Divorce, Intimacy, Childcare & Affairs
# ---------------------------------------------------------------------------

async def marry_citizens(proposer: Player, partner: Player) -> dict[str, Any]:
    """Official marriage between two citizens (requires level >= 3)."""
    if proposer.user_id == partner.user_id:
        return {"success": False, "message": "❌ نمی‌توانید با خودتان ازدواج کنید!"}

    if proposer.spouse_id is not None:
        return {"success": False, "message": "❌ شما هم‌اکنون متأهل هستید! تیرامیکس تک‌همسری است."}

    if partner.spouse_id is not None:
        return {"success": False, "message": f"❌ {partner.display_name} قبلاً ازدواج کرده است!"}

    if proposer.level < 3 or partner.level < 3:
        return {"success": False, "message": "⚠️ برای ثبت ازدواج رسمی، هر دو طرف باید حداقل به <b>لول ۳</b> رسیده باشند."}

    now = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET spouse_id = ?, marriage_date = ? WHERE user_id = ?",
            (partner.user_id, now, proposer.user_id),
        )
        await conn.execute(
            "UPDATE players SET spouse_id = ?, marriage_date = ? WHERE user_id = ?",
            (proposer.user_id, now, partner.user_id),
        )

    return {
        "success": True,
        "message": f"💍 <b>پیوند آسمانی در شهرداری تیرامیکس!</b>\n\n"
                   f"عقد رسمی بین <b>{proposer.display_name}</b> و <b>{partner.display_name}</b> با شکوه تمام ثبت شد! 💐🕊\n"
                   f"از این پس می‌توانید از دستورات مشترک زندگی و فرزندآوری استفاده کنید.",
    }


async def divorce_citizens(user1_id: int, user2_id: int) -> dict[str, Any]:
    """Execute legal divorce."""
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET spouse_id = NULL, marriage_date = 0 WHERE user_id IN (?, ?)",
            (user1_id, user2_id),
        )

    return {
        "success": True,
        "message": "⚖️ <b>حکم قطعی طلاق صادر شد!</b>\n\nپیوند زناشویی بین طرفین باطل گردید و هر دو طرف مجدداً مجرد شدند.",
    }


async def execute_intimacy(p1: Player, p2: Player) -> dict[str, Any]:
    """Intimacy between married couple with 7% pregnancy chance."""
    now = int(time.time())
    if p1.spouse_id != p2.user_id or p2.spouse_id != p1.user_id:
        return {"success": False, "message": "❌ این عمل فقط بین زن و شوهر رسمی مجاز است!"}

    elapsed = now - p1.last_intimacy_time
    if elapsed < INTIMACY_COOLDOWN:
        rem_hours = int((INTIMACY_COOLDOWN - elapsed) // 3600)
        rem_min = int(((INTIMACY_COOLDOWN - elapsed) % 3600) // 60)
        return {
            "success": False,
            "message": f"⏳ رابطه فقط هر ۸ ساعت یک‌بار مجاز است.\nزمان باقی‌مانده: <b>{rem_hours} ساعت و {rem_min} دقیقه</b>.",
        }

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET last_intimacy_time = ? WHERE user_id IN (?, ?)",
            (now, p1.user_id, p2.user_id),
        )

    # Check pregnancy chance: 7%
    # Determine who is female
    female = p1 if p1.gender in ("female", "دختر", "زن") else (p2 if p2.gender in ("female", "دختر", "زن") else None)
    pregnancy_happened = False

    if female and not female.is_pregnant(now):
        if random.random() <= 0.07:
            pregnancy_happened = True
            pregnant_until = now + PREGNANCY_DURATION
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET is_pregnant_until = ? WHERE user_id = ?",
                    (pregnant_until, female.user_id),
                )

    if pregnancy_happened:
        return {
            "success": True,
            "pregnancy": True,
            "female_name": female.display_name if female else "همسر",
            "message": f"💕 <b>شبی رمانتیک و فراموش‌نشدنی...</b>\n\n"
                       f"🍼 <b>خبر بزرگ! آزمایش بارداری {female.display_name if female else 'همسر'} مثبت شد!</b>\n"
                       f"فرزند شما در راه است. برای مراقبت و زایمان ایمن حتماً به <code>کلینیک</code> مراجعه کنید.",
        }
    else:
        return {
            "success": True,
            "pregnancy": False,
            "message": "💕 <b>لحظاتی گرم و آرام در کانون خانواده سپری شد.</b>\n(شانس بارداری این بار رخ نداد).",
        }


async def clinic_service(player: Player) -> dict[str, Any]:
    """Visit clinic for healthcare, delivery or checkup."""
    now = int(time.time())
    creds, _ = await economy.balances(player.user_id)

    if player.is_pregnant(now):
        # Baby delivery
        if creds < DELIVERY_FEE:
            return {
                "success": False,
                "message": f"❌ هزینه زایمان و بستری در کلینیک <b>{DELIVERY_FEE:,}</b> سکه است! (موجودی شما: {creds:,} سکه)",
            }
        await economy.spend(player.user_id, credits=DELIVERY_FEE, kind=ActivityKind.CLINIC)
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET is_pregnant_until = 0, children_count = children_count + 1 WHERE user_id = ?",
                (player.user_id,),
            )
            if player.spouse_id:
                await conn.execute(
                    "UPDATE players SET children_count = children_count + 1 WHERE user_id = ?",
                    (player.spouse_id,),
                )
        return {
            "success": True,
            "action": "delivery",
            "message": f"👶🎉 <b>تبریک فراوان! زایمان در کلینیک تیرامیکس با موفقیت انجام شد!</b>\n\n"
                       f"قدم نورسیده مبارک! فرزند دلبندتان به سلامت متولد شد.\n"
                       f"تعداد فرزندان خانواده: <b>{player.children_count + 1}</b> فرزند.",
        }
    else:
        # Regular medical checkup / heal
        if creds < CLINIC_HEAL_FEE:
            return {
                "success": False,
                "message": f"❌ هزینه ویزیت پزشک <b>{CLINIC_HEAL_FEE:,}</b> سکه است.",
            }
        await economy.spend(player.user_id, credits=CLINIC_HEAL_FEE, kind=ActivityKind.CLINIC)
        return {
            "success": True,
            "action": "checkup",
            "message": f"🩺 <b>چکاپ کامل سلامت در کلینیک تیرامیکس</b>\n\n"
                       f"پزشک علائم حیاتی شما را بررسی کرد: وضعیت سلامتی کاملاً پایدار و عالی است!\n"
                       f"هزینه ویزیت و دارو: <b>{CLINIC_HEAL_FEE:,}</b> سکه.",
        }


async def attempt_affair(cheater: Player, partner: Player, victim_spouse: Player) -> dict[str, Any]:
    """Affair between a married person and another player with detection odds."""
    now = int(time.time())
    if cheater.user_id == partner.user_id:
        return {"success": False, "message": "❌ نمی‌توانید با خودتان رابطه پنهانی داشته باشید!"}

    elapsed = now - cheater.last_affair_time
    if elapsed < AFFAIR_COOLDOWN:
        rem_hours = int((AFFAIR_COOLDOWN - elapsed) // 3600)
        return {"success": False, "message": f"⏳ پلیس اخلاق یا شک همسر در کمین است! حداقل <b>{rem_hours} ساعت</b> صبر کنید."}

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET last_affair_time = ? WHERE user_id = ?",
            (now, cheater.user_id),
        )

    # Intel calculation: average intelligence of cheaters vs spouse intelligence
    avg_cheaters_intel = (cheater.education_level + cheater.level + partner.education_level + partner.level) / 2.0
    spouse_intel = (victim_spouse.education_level * 1.5) + victim_spouse.level

    # Probability of being caught: if spouse is smarter, higher detection chance
    intel_diff = spouse_intel - avg_cheaters_intel
    caught_chance = 0.40 + (intel_diff * 0.05)
    caught_chance = max(0.15, min(0.85, caught_chance))

    if random.random() <= caught_chance:
        # BUSTED!
        return {
            "success": False,
            "busted": True,
            "message": f"🚨😱 <b>رسوایی و لو رفتن خیانت در شهر تیرامیکس!</b>\n\n"
                       f"همسر تیزهوش ({victim_spouse.display_name}) با ردیابی شواهد، مچ {cheater.display_name} را "
                       f"در قرار پنهانی با {partner.display_name} گرفت!\n"
                       f"هم‌اکنون همسر می‌تواند با دستور <code>طلاق</code> دادخواست جدایی دهد!",
        }
    else:
        # Secret kept safe — count it toward the city's cheater record.
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET affair_count = affair_count + 1 "
                "WHERE user_id = ?",
                (cheater.user_id,),
            )
        return {
            "success": True,
            "busted": False,
            "message": f"🤫 <b>ملاقات مخفیانه بدون ردپا انجام شد!</b>\n\n"
                       f"همسر متوجه هیچ سرنخی نشد.",
        }


async def affair_record() -> str:
    """رکورد خیانتکارها — the leaderboard shown with ``خیانت`` (phase 4)."""
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT display_name, affair_count FROM players "
            "WHERE affair_count > 0 "
            "ORDER BY affair_count DESC, user_id LIMIT ?",
            (settings.affair_record_top,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    if not rows:
        return (
            "🏆 <b>رکورد خیانتکارها:</b> "
            "هنوز کسی ردپایی از خودش باقی نذاشته."
        )
    lines = [
        f"{index}. {row['display_name']} — "
        f"<b>{row['affair_count']}</b> خیانت مخفی"
        for index, row in enumerate(rows, 1)
    ]
    return "🏆 <b>رکورد خیانتکارها:</b>\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Appearance: Barber Shop & Plastic Surgery
# ---------------------------------------------------------------------------

async def update_hairstyle(player: Player, style_key: str, color_key: str) -> dict[str, Any]:
    """Change hairstyle and hair color at the local salon."""
    creds, _ = await economy.balances(player.user_id)
    if creds < HAIRSTYLE_FEES:
        return {"success": False, "message": f"❌ هزینه پیرایش و رنگ مو <b>{HAIRSTYLE_FEES:,}</b> سکه است."}

    await economy.spend(player.user_id, credits=HAIRSTYLE_FEES, kind=ActivityKind.SALON)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET hair_style = ?, hair_color = ? WHERE user_id = ?",
            (style_key, color_key, player.user_id),
        )

    return {
        "success": True,
        "style": style_key,
        "color": color_key,
        "fee": HAIRSTYLE_FEES,
        "message": f"✂️ <b>مدل موی جدید در آرایشگاه تیرامیکس اعمال شد!</b>\nهزینه: <b>{HAIRSTYLE_FEES:,}</b> سکه.",
    }


async def update_facial_surgery(player: Player, eye_style: str, eye_color: str, mouth_style: str) -> dict[str, Any]:
    """Plastic surgery for eyes and facial expression."""
    creds, _ = await economy.balances(player.user_id)
    if creds < SURGERY_FEES:
        return {"success": False, "message": f"❌ هزینه جراحی زیبایی چهره <b>{SURGERY_FEES:,}</b> سکه است."}

    await economy.spend(player.user_id, credits=SURGERY_FEES, kind=ActivityKind.SALON)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET eye_style = ?, eye_color = ?, mouth_style = ? WHERE user_id = ?",
            (eye_style, eye_color, mouth_style, player.user_id),
        )

    return {
        "success": True,
        "fee": SURGERY_FEES,
        "message": f"💎 <b>عمل زیبایی با موفقیت در کلینیک زیبایی تیرامیکس انجام شد!</b>\nفرم چشم‌ها و لبخند شما ارتقا یافت.",
    }


# ---------------------------------------------------------------------------
# Clans
# ---------------------------------------------------------------------------

async def create_new_clan(leader: Player, group_id: int, clan_name: str) -> dict[str, Any]:
    """Found a new local clan/syndicate in the group."""
    if leader.clan_id is not None:
        return {"success": False, "message": "❌ شما قبلاً در یک کلن عضویت دارید!"}

    clean_name = clan_name.strip()
    if len(clean_name) < 3 or len(clean_name) > 30:
        return {"success": False, "message": "❌ نام کلن باید بین ۳ تا ۳۰ کاراکتر باشد."}

    creation_cost = 5000
    creds, _ = await economy.balances(leader.user_id)
    if creds < creation_cost:
        return {"success": False, "message": f"❌ هزینه تأسیس کلن <b>{creation_cost:,}</b> سکه است. (موجودی: {creds:,} سکه)"}

    await economy.spend(leader.user_id, credits=creation_cost, kind=ActivityKind.CLAN)

    async with db.write() as conn:
        cursor = await conn.execute(
            "INSERT INTO clans (group_id, name, leader_id, treasury) VALUES (?, ?, ?, 0)",
            (group_id, clean_name, leader.user_id),
        )
        clan_id = cursor.lastrowid
        await cursor.close()

        await conn.execute(
            "UPDATE players SET clan_id = ?, clan_role = 'leader' WHERE user_id = ?",
            (clan_id, leader.user_id),
        )

    return {
        "success": True,
        "clan_id": clan_id,
        "clan_name": clean_name,
        "message": f"🛡️👑 <b>کلن «{clean_name}» با موفقیت در این گروه تأسیس شد!</b>\n\n"
                   f"لیدر: <b>{leader.display_name}</b>\n"
                   f"اعضای دیگر می‌توانند با تایید شما به کلن بپیوندند.",
    }


async def get_player_clan(clan_id: int | None) -> dict[str, Any] | None:
    """Fetch clan details."""
    if clan_id is None:
        return None
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT clan_id, group_id, name, leader_id, treasury FROM clans WHERE clan_id = ?",
            (clan_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return dict(row)
