"""قاعده سراسری فراخوانی دستورات متنی: پیام باید «خودِ فرمان» یا «فرمان + آرگومان مستند» باشد.

هیچ جمله گفتگویی (مثل «کار من خیلی سخته») نباید باعث واکنش ربات شود؛
این قاعده یک‌بار در CommandOrText طراحی شده و در تمام کلیدها استفاده می‌شود.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from handlers.common import CommandOrText, extract_args

# ---------------------------------------------------------------------------
# ۱. قاعده خالی بودن پیام (پیام = فقط فرمان) — قلب درخواست
# ---------------------------------------------------------------------------


def _work_filter() -> CommandOrText:
    return CommandOrText(["work", "shift"], {"کار", "شیفت", "work", "shift"})


def test_sentence_with_command_word_is_not_a_command():
    f = _work_filter()
    # گزارش اصلی کاربر: «کار من خیلی سخته» نباید کار را اجرا کند
    assert f.match_text("کار من خیلی سخته") is False
    assert f.match_text("کار خیلی خوبی بود") is False
    assert f.match_text("امروز کار زیادی داشتم") is False
    # خودِ فرمان در پیام خالی → بله
    assert f.match_text("کار") is True
    assert f.match_text("  کار  ") is True
    # فرم اسلش با آرگومان → بله (اعلام صریح فرمان)
    assert f.match_text("/work") is True
    assert f.match_text("/work@IgrisLifeBot") is True


def test_common_words_in_sentences_do_not_trigger():
    cases = {
        "پول ندارم": (["balance", "bal"], {"موجودی", "پول", "سکه"}),
        "خرید من اشتباه بود": (["shop"], {"شاپ", "فروشگاه", "خرید", "بازار"}),
        "کمک لازم دارم": (["help"], {"راهنما", "کمک", "آموزش"}),
    }
    for sentence, (cmds, words) in cases.items():
        f = CommandOrText(cmds, words)
        assert f.match_text(sentence) is False, sentence


def test_bare_command_still_matches():
    assert CommandOrText(["balance"], {"موجودی", "پول"}).match_text("موجودی") is True
    assert CommandOrText(["help"], {"راهنما", "کمک"}).match_text("راهنما") is True


# ---------------------------------------------------------------------------
# ۲. آرگومان‌های مستند مجازند؛ متن جمله‌ای نه
# ---------------------------------------------------------------------------


def test_structured_args_are_allowed():
    steal = CommandOrText(["steal", "robbery"], {"دزدی", "جیب‌بری"})
    assert steal.match_text("دزدی @ali") is True
    assert steal.match_text("دزدی 123456789") is True
    assert steal.match_text("دزدی") is True
    assert steal.match_text("دزدی منو بزن") is False

    heist = CommandOrText(["heist", "rob"], prefix_words=("سرقت", "heist"))
    assert heist.match_text("سرقت 500") is True
    assert heist.match_text("سرقت 100k") is True
    assert heist.match_text("سرقت من خیلی سخته") is False


def test_arg_words_and_name_tail():
    dice = CommandOrText(
        ["dice"], words={"تاس"}, arg_words={"بالا", "پایین", "high", "low"}
    )
    assert dice.match_text("تاس 500 بالا") is True
    assert dice.match_text("تاس 500") is True
    assert dice.match_text("تاس 500 گند زدم") is False

    market = CommandOrText(
        ["exchange"], words={"بورس"}, arg_words={"خرید", "فروش", "شرط"}, name_tail=True
    )
    assert market.match_text("بورس خرید طلا 500") is True
    assert market.match_text("بورس فروش نفت") is True
    # نام آزاد فقط بعد از کلمه کلیدی مجاز است
    assert market.match_text("بورس من خیلی ضرر کردم") is False


def test_arg_names_catalog_matching():
    names = ["پیتزا مخصوص", "کباب کوبیده"]
    food = CommandOrText(["food"], words={"غذا"}, arg_names=names)
    assert food.match_text("غذا پیتزا") is True
    assert food.match_text("غذا کباب کوبیده") is True
    assert food.match_text("غذا خیلی خوب بود") is False


def test_arg_pattern_allows_codes_only():
    gift = CommandOrText(
        ["gift"],
        words={"هدیه"},
        arg_words={"ساخت"},
        name_tail=True,
        arg_pattern=r"[A-Za-z0-9_\-]{2,40}",
    )
    assert gift.match_text("هدیه GIFT2024") is True
    assert gift.match_text("هدیه ساخت کد من 500 3") is True
    assert gift.match_text("هدیه تولدت مبارک") is False


# ---------------------------------------------------------------------------
# ۳. چندکلمه‌ای‌ها و استخراج آرگومان
# ---------------------------------------------------------------------------


def test_multi_word_triggers_match_and_extract_args():
    transfer = CommandOrText(
        ["transfer"], {"انتقال", "کارت به کارت", "واریز به", "انتقال وجه"}
    )
    assert transfer.match_text("کارت به کارت 500 @ali") is True
    assert transfer.match_text("کارت به کارت") is True

    msg = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="A"),
        text="کارت به کارت 500 @ali",
    )
    assert extract_args(msg) == ["500", "@ali"]


def test_extract_args_skips_trigger_phrase():
    msg = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="A"),
        text="بازار سیاه خرید شمشیر",
    )
    assert extract_args(msg) == ["خرید", "شمشیر"]


# ---------------------------------------------------------------------------
# ۴. یک قاعده برای همه: دروازهبان گروه هم از همان مرجع استفاده می‌کند
# ---------------------------------------------------------------------------


def test_gatekeeper_uses_same_rule():
    import handlers  # noqa: F401  # ثبت تمام فیلترها در رجیستری مشترک

    assert CommandOrText.is_game_command("پروفایل") is True
    assert CommandOrText.is_game_command("کار") is True
    assert CommandOrText.is_game_command("کار من خیلی سخته") is False
    assert CommandOrText.is_game_command("سلام دوستان چطورید") is False


@pytest.mark.asyncio
async def test_gatekeeper_silent_on_sentences(monkeypatch):
    """کاربر تازه‌وارد در گروه جمله عادی می‌فرستد → هیچ پاسخی (بدون اسپم)."""
    from database.connection import db
    from database.seed import seed_catalog
    from handlers.gatekeeper import OnboardingGateMiddleware
    from services import game

    await db.connect()
    await seed_catalog()
    try:
        middleware = OnboardingGateMiddleware()
        user = User(id=998877, is_bot=False, first_name="New", username="newu")
        group = Chat(id=-1001, type="supergroup", title="G")
        await game.ensure_player(998877, "New", "newu")

        replies: list[str] = []

        async def mock_reply(self, text, **kwargs):
            replies.append(text)
            return self

        monkeypatch.setattr(Message, "reply", mock_reply)

        mock_bot = MagicMock()
        mock_me = MagicMock()
        mock_me.username = "IgrisLifeBot"
        mock_bot.get_me = AsyncMock(return_value=mock_me)

        msg = Message(
            message_id=90,
            date=datetime.now(UTC),
            chat=group,
            from_user=user,
            text="کار من خیلی سخته",
        )
        called = False

        async def dummy_handler(event, data):
            nonlocal called
            called = True

        result = await middleware(dummy_handler, msg, {"bot": mock_bot})
        assert result is None
        assert called is False
        assert replies == []

        # اما خودِ فرمان همچنان دروازهبان را فعال می‌کند
        msg_cmd = Message(
            message_id=91,
            date=datetime.now(UTC),
            chat=group,
            from_user=user,
            text="کار",
        )
        await middleware(dummy_handler, msg_cmd, {"bot": mock_bot})
        assert called is False  # هندلر اصلی بازی اجرا نمی‌شود؛ فقط پرامپت ثبت‌نام
        assert len(replies) == 1
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# ۵. اتصال سرتاسری: پیام جمله‌ای از کاربر بازیکن هم بی‌صدا می‌ماند
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_ignores_sentence_messages(monkeypatch):
    from database.connection import db
    from database.seed import seed_catalog
    from handlers import economy as economy_h
    from services import game

    await db.connect()
    await seed_catalog()
    try:
        dp = Dispatcher()
        # جداسازی ایمن روتر singleton تا تست‌های دیگر هم بتوانند ثبتش کنند
        economy_h.router._parent_router = None  # noqa: SLF001
        dp.include_router(economy_h.router)
        try:
            replies: list[str] = []

            async def fake_reply(self, text, **kwargs):
                replies.append(text)
                return self

            monkeypatch.setattr(Message, "reply", fake_reply)

            bot = Bot(token="123456789:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQR")
            chat = Chat(id=123, type="private")
            user = User(id=123, is_bot=False, first_name="Aria", username="aria")
            now = datetime.now(UTC)

            await game.ensure_player(123, "Aria", "aria")
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET onboarding_completed = 1 WHERE user_id = 123"
                )

            for idx, text in enumerate(
                ["کار من خیلی سخته", "پول ندارم", "کمک لازم دارم"], start=1
            ):
                msg = Message(
                    message_id=idx, date=now, chat=chat, from_user=user, text=text
                )
                await dp.feed_update(bot=bot, update=Update(update_id=idx, message=msg))
            assert replies == []

            # خودِ فرمان همچنان جواب می‌گیرد
            msg = Message(message_id=9, date=now, chat=chat, from_user=user, text="کار")
            await dp.feed_update(bot=bot, update=Update(update_id=9, message=msg))
            assert len(replies) == 1
        finally:
            economy_h.router._parent_router = None  # noqa: SLF001
    finally:
        await db.close()
