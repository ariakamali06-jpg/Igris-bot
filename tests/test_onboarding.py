import pytest
from datetime import datetime, timezone
from aiogram.types import Chat, Message, User
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from pydantic import ConfigDict, PrivateAttr

from database.connection import db
from database.seed import seed_catalog
from handlers import onboarding
from services import game


class FakeCall:
    def __init__(self, data: str, message: Message, user: User) -> None:
        self.data = data
        self.message = message
        self.from_user = user
        self.alerts: list[str] = []
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kw) -> None:
        self.answers.append(text)
        if show_alert:
            self.alerts.append(text)


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    @property
    def replies(self) -> list:
        return self._replies

    def __init__(self, text: str = "", user: User | None = None) -> None:
        super().__init__(
            message_id=101,
            date=datetime.now(timezone.utc),
            chat=Chat(id=999001, type="private"),
            from_user=user or User(id=999001, is_bot=False, first_name="Aria", username="ariakamali"),
            text=text,
        )
        self._replies = []

    async def reply(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def answer(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def reply_photo(self, photo=None, caption=None, reply_markup=None, **kw):
        self._replies.append({"caption": caption, "reply_markup": reply_markup})
        return self


@pytest.fixture(scope="session")
async def _db():
    await db.connect()
    await seed_catalog()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_onboarding_full_wizard(_db) -> None:
    async with db.write() as conn:
        await conn.execute("DELETE FROM players WHERE user_id = 999001")
        await conn.execute("DELETE FROM loadout WHERE user_id = 999001")
        await conn.execute("DELETE FROM inventory WHERE user_id = 999001")
        await conn.execute("DELETE FROM wallets WHERE user_id = 999001")

    storage = MemoryStorage()
    user = User(id=999001, is_bot=False, first_name="Aria", username="ariakamali")
    key = StorageKey(bot_id=123456, chat_id=999001, user_id=999001)
    state = FSMContext(storage=storage, key=key)

    # 1. /start triggers onboarding when not completed
    msg = MutableMessage(text="/start", user=user)
    await onboarding.cmd_start(msg, state)
    assert len(msg.replies) == 1
    assert "سفارشی‌سازی" in msg.replies[0]["text"]
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_name.state

    # 2. Select name via telegram button
    call_name = FakeCall("ob:name_tg", msg, user)
    await onboarding.cb_name_telegram(call_name, state)
    data = await state.get_data()
    assert data["name"] == "Aria"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_gender.state

    # 3. Choose gender
    call_gen = FakeCall("ob:gen:مرد", msg, user)
    await onboarding.cb_gender(call_gen, state)
    data = await state.get_data()
    assert data["gender"] == "مرد"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_age.state

    # 4. Choose age
    call_age = FakeCall("ob:age:22", msg, user)
    await onboarding.cb_age(call_age, state)
    data = await state.get_data()
    assert data["age"] == 22
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_body.state

    # 5. Choose body & skin
    call_body = FakeCall("ob:body:base_male:fair", msg, user)
    await onboarding.cb_body(call_body, state)
    data = await state.get_data()
    assert data["body_stance"] == "base_male"
    assert data["skin_tone"] == "fair"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_eyes.state

    # 6. Choose eyes
    call_eyes = FakeCall("ob:eyes:blue", msg, user)
    await onboarding.cb_eyes(call_eyes, state)
    data = await state.get_data()
    assert data["eye_color"] == "blue"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_hair.state

    # 7. Choose hair style
    call_hair = FakeCall("ob:hair:street_fade", msg, user)
    await onboarding.cb_hair(call_hair, state)
    data = await state.get_data()
    assert data["hair_style"] == "street_fade"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_hair_color.state

    # 8. Choose hair color
    call_hair_color = FakeCall("ob:hairc:silver", msg, user)
    await onboarding.cb_hair_color(call_hair_color, state)
    data = await state.get_data()
    assert data["hair_color"] == "silver"
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_kit.state

    # 9. Choose kit & finalize
    call_kit = FakeCall("ob:kit:street", msg, user)
    await onboarding.cb_kit(call_kit, state)
    assert await state.get_state() is None

    # Check player in DB
    player = await game.load_player(user.id)
    assert player is not None
    assert player.display_name == "Aria"
    assert player.gender == "مرد"
    assert player.age == 22
    assert player.body_stance == "base_male"
    assert player.skin_tone == "fair"
    assert player.eye_color == "blue"
    assert player.hair_color == "silver"
    assert player.onboarding_completed == 1
    assert player.loadout["head"] == "street_fade"
    assert player.loadout["body"] == "fitted_tee"
    assert player.loadout["legs"] == "street_slacks"

    # 10. Subsequent /start directly opens profile
    msg2 = MutableMessage(text="/start", user=user)
    await onboarding.cmd_start(msg2, state)
    # Profile should be sent (card/profile)
    assert len(msg2.replies) == 1


@pytest.mark.asyncio
async def test_onboarding_custom_typing_and_shadow_kit(_db) -> None:
    async with db.write() as conn:
        await conn.execute("DELETE FROM players WHERE user_id = 999002")
        await conn.execute("DELETE FROM loadout WHERE user_id = 999002")
        await conn.execute("DELETE FROM inventory WHERE user_id = 999002")
        await conn.execute("DELETE FROM wallets WHERE user_id = 999002")

    storage = MemoryStorage()
    user = User(id=999002, is_bot=False, first_name="Shadow", username="shadow_king")
    key = StorageKey(bot_id=123456, chat_id=999002, user_id=999002)
    state = FSMContext(storage=storage, key=key)

    # 1. /start triggers onboarding
    msg = MutableMessage(text="/start", user=user)
    await onboarding.cmd_start(msg, state)

    # 2. Type custom name
    name_msg = MutableMessage(text="پادشاه سایه‌ها", user=user)
    await onboarding.msg_name_text(name_msg, state)
    data = await state.get_data()
    assert data["name"] == "پادشاه سایه‌ها"

    # 3. Choose female/other
    call_gen = FakeCall("ob:gen:زن", msg, user)
    await onboarding.cb_gender(call_gen, state)

    # 4. Type custom age
    age_msg = MutableMessage(text="25", user=user)
    await onboarding.msg_age_text(age_msg, state)
    data = await state.get_data()
    assert data["age"] == 25

    # 5. Choose female body
    call_body = FakeCall("ob:body:base_female:tan", msg, user)
    await onboarding.cb_body(call_body, state)

    # 6. Choose eyes
    call_eyes = FakeCall("ob:eyes:red", msg, user)
    await onboarding.cb_eyes(call_eyes, state)

    # 7. Choose Raven Shag hair
    call_hair = FakeCall("ob:hair:raven_shag", msg, user)
    await onboarding.cb_hair(call_hair, state)

    # 8. Choose Hair color
    call_hair_color = FakeCall("ob:hairc:crimson", msg, user)
    await onboarding.cb_hair_color(call_hair_color, state)

    # 9. Choose Shadow kit
    call_kit = FakeCall("ob:kit:shadow", msg, user)
    await onboarding.cb_kit(call_kit, state)

    player = await game.load_player(user.id)
    assert player is not None
    assert player.display_name == "پادشاه سایه‌ها"
    assert player.gender == "زن"
    assert player.age == 25
    assert player.body_stance == "base_female"
    assert player.skin_tone == "tan"
    assert player.eye_color == "red"
    assert player.hair_color == "crimson"
    assert player.loadout["head"] == "raven_shag"
    assert player.loadout["body"] == "hunter_trench"
    assert player.loadout["legs"] == "techwear_cargo"


@pytest.mark.asyncio
async def test_recreation_restriction_for_regular_users_and_admin(_db) -> None:
    from handlers import profile

    reg_user_id = 888001
    owner_user_id = 5765828495

    async with db.write() as conn:
        await conn.execute("DELETE FROM players WHERE user_id IN (?, ?)", (reg_user_id, owner_user_id))
        await conn.execute("DELETE FROM loadout WHERE user_id IN (?, ?)", (reg_user_id, owner_user_id))
        await conn.execute("DELETE FROM inventory WHERE user_id IN (?, ?)", (reg_user_id, owner_user_id))
        await conn.execute("DELETE FROM wallets WHERE user_id IN (?, ?)", (reg_user_id, owner_user_id))

    storage = MemoryStorage()

    # 1. Create a regular player with onboarding completed
    reg_user = User(id=reg_user_id, is_bot=False, first_name="RegularPlayer", username="regular")
    await game.ensure_player(reg_user_id, "RegularPlayer", "regular")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = ?", (reg_user_id,))
    reg_player = await game.load_player(reg_user_id)
    assert reg_player is not None
    assert reg_player.onboarding_completed == 1

    # Check profile markup for regular player: NO act:create button!
    reg_markup = await profile._profile_markup(reg_player)
    reg_cb_data = [b.callback_data for row in reg_markup.inline_keyboard for b in row]
    assert "act:create" not in reg_cb_data

    # Regular player tries /create command -> REJECTED
    reg_state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=reg_user_id, user_id=reg_user_id))
    msg_create = MutableMessage(text="/create", user=reg_user)
    await onboarding.cmd_start(msg_create, reg_state)
    assert len(msg_create.replies) == 1
    assert "قفل" in msg_create.replies[0]["text"]
    assert await reg_state.get_state() is None

    # Regular player taps act:create callback -> REJECTED with alert
    call_create = FakeCall("act:create", msg_create, reg_user)
    await onboarding.cb_start_creation(call_create, reg_state)
    assert len(call_create.alerts) == 1
    assert "قفل" in call_create.alerts[0]
    assert await reg_state.get_state() is None

    # 2. Check Owner (Rex Lapis: 5765828495)
    owner_user = User(id=owner_user_id, is_bot=False, first_name="Rex Lapis", username="rexlapis")
    await game.ensure_player(owner_user_id, "Rex Lapis", "rexlapis")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = ?", (owner_user_id,))
    owner_player = await game.load_player(owner_user_id)
    assert owner_player is not None
    assert owner_player.onboarding_completed == 1

    # Check profile markup for owner: HAS act:create button!
    owner_markup = await profile._profile_markup(owner_player)
    owner_cb_data = [b.callback_data for row in owner_markup.inline_keyboard for b in row]
    assert "act:create" in owner_cb_data

    # Owner runs /create -> ALLOWED into wizard!
    owner_state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=owner_user_id, user_id=owner_user_id))
    msg_owner_create = MutableMessage(text="/create", user=owner_user)
    await onboarding.cmd_start(msg_owner_create, owner_state)
    assert len(msg_owner_create.replies) == 1
    assert "سفارشی‌سازی" in msg_owner_create.replies[0]["text"]
    assert await owner_state.get_state() == onboarding.OnboardingState.waiting_for_name.state

    # Owner taps act:create callback -> ALLOWED into wizard!
    owner_state_2 = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=owner_user_id, user_id=owner_user_id))
    call_owner = FakeCall("act:create", msg_owner_create, owner_user)
    await onboarding.cb_start_creation(call_owner, owner_state_2)
    assert len(call_owner.alerts) == 0
    assert await owner_state_2.get_state() == onboarding.OnboardingState.waiting_for_name.state
