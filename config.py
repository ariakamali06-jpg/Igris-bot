"""Application configuration.

Every magic number that governs the game economy lives here so balance
tuning never requires touching gameplay code.  Values are loaded from
environment variables (optionally from a ``.env`` file) and frozen after
startup so services can read them without worrying about mutation.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime settings, hydrated from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Core ---------------------------------------------------------------
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    # NoDecode: pydantic-settings must not JSON-decode the env var, otherwise
    # "123456" parses to int and "1,2,3" raises before our validator runs.
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, alias="ADMIN_IDS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    # --- Channels & Sponsorship ---------------------------------------------
    force_channel_join: bool = Field(default=True, alias="FORCE_CHANNEL_JOIN")
    required_channels: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {
                "name": "کار و وار (ربات ایگریس)",
                "url": "https://t.me/kar_O_war",
                "username": "@kar_O_war",
            },
            {
                "name": "API CONFIGG",
                "url": "https://t.me/APICONFIGG",
                "username": "@APICONFIGG",
            },
            {
                "name": "فرمول وان فارسی",
                "url": "https://t.me/formula_one_farsi",
                "username": "@formula_one_farsi",
            },
        ],
        alias="REQUIRED_CHANNELS",
    )

    # Paths
    db_path: Path = Field(default=ROOT_DIR / "data" / "game.db", alias="DB_PATH")
    assets_dir: Path = Field(
        default=ROOT_DIR / "assets" / "layers", alias="ASSETS_DIR"
    )
    manifest_path: Path = Field(
        default=ROOT_DIR / "assets" / "manifest.json", alias="MANIFEST_PATH"
    )
    cache_dir: Path = Field(default=ROOT_DIR / "cache" / "composites", alias="CACHE_DIR")

    # --- Character card -----------------------------------------------------
    canvas_size: tuple[int, int] = (512, 512)
    hud_height: int = 96
    render_budget_ms: float = 50.0

    # --- Progression --------------------------------------------------------
    base_energy: int = 100
    max_energy: int = 200
    energy_regen_per_minute: float = 2.0
    energy_regen_interval_seconds: float = 60.0

    base_atk: int = 10
    base_def: int = 8
    base_drip: int = 5

    exp_per_level: int = 100
    level_exp_growth: float = 1.35
    level_atk_bonus: int = 2
    level_def_bonus: int = 2
    level_drip_bonus: int = 1

    # --- Activity payouts ---------------------------------------------------
    chat_exp_per_message: int = 1
    chat_exp_cooldown_seconds: int = 20
    duel_exp_win: int = 40
    duel_exp_loss: int = 12
    duel_energy_cost: int = 10
    raid_energy_cost: int = 5

    # /work — energy-gated credit faucet scaled by level.
    work_energy_cost: int = 15
    work_credits_base: int = 35
    work_credits_per_level: int = 6
    work_exp: int = 8
    cooldown_work: int = 30

    # Daily streak claim.
    daily_exp: int = 25
    cooldown_daily: int = 10

    # --- Raid / boss --------------------------------------------------------
    raid_spawn_min_messages: int = 60
    raid_spawn_max_messages: int = 100
    raid_dps_energy_cost: int = 5
    raid_min_participants: int = 1
    raid_hp_base: int = 400
    raid_hp_per_level: int = 60
    raid_respawn_delay_seconds: int = 60
    raid_hit_exp: int = 4
    raid_loot_credits_per_percent: int = 6
    raid_loot_shards: int = 3
    raid_loot_exp: int = 30

    # --- Economy ------------------------------------------------------------
    starting_credits: int = 250
    starting_soul_shards: int = 0
    shop_credits_per_exp: int = 5
    shop_rotation_size: int = 6
    daily_claim_credits: int = 150
    daily_claim_shards: int = 2
    drip_discount_per_point: float = 0.002
    drip_discount_cap: float = 0.15
    casino_min_bet: int = 10
    casino_max_bet: int = 50_000
    cooldown_casino_bet: int = 3

    # House edge (0.05 == 5% edge in the house's favour).
    dice_house_edge: float = 0.05
    coinflip_house_edge: float = 0.03
    heist_house_edge: float = 0.10
    duel_house_rake: float = 0.05

    # Heist risk band.
    heist_energy_cost: int = 10
    heist_base_success: float = 0.55
    heist_min_success: float = 0.15
    heist_max_success: float = 0.90
    heist_arrest_chance: float = 0.12
    heist_arrest_energy_cost: int = 20
    heist_stake_min: int = 50
    heist_stake_max: int = 25_000
    heist_payout_multiplier: float = 1.8

    # --- Ocean port phase 1: passive economy (املاک / نیرو) -----------------
    property_income_interval: int = 1800
    property_max_level: int = 10
    property_level_income_bonus: float = 0.30  # +30% income per upgrade level
    property_max_pending_ticks: int = 96  # accrual cap so idle income cannot explode
    property_upgrade_base_cost: int = 2_000
    property_upgrade_cost_growth: float = 1.5
    cooldown_property_buy: int = 3
    cooldown_property_upgrade: int = 3
    cooldown_property_collect: int = 5

    # --- Ocean port phase 1: daily contracts (قرارداد) ----------------------
    contract_window_seconds: int = 86_400
    contract_count: int = 3
    contract_target_work: int = 3
    contract_target_study: int = 1
    contract_target_shop: int = 1
    contract_target_theft: int = 1
    contract_reward_credits: int = 350
    contract_reward_exp: int = 30

    # --- Ocean port phase 1: exchange (بورس) --------------------------------
    market_hour_seconds: int = 3600
    market_volatility: float = 0.08
    market_price_min_factor: float = 0.5
    market_price_max_factor: float = 2.0
    market_trade_min: int = 10
    market_trade_max: int = 500_000
    market_bet_min: int = 10
    market_bet_max: int = 25_000
    market_bet_house_edge: float = 0.08
    market_bet_win_prob: float = 0.5
    cooldown_market_trade: int = 3
    cooldown_market_bet: int = 10

    # --- Ocean port phase 1: anti-theft cover (سپر) --------------------------
    shield_duration_seconds: int = 86_400
    shield_cost_pct: float = 0.05
    shield_cost_min: int = 150
    shield_cost_max: int = 7_500
    cooldown_shield: int = 30

    # --- Ocean port phase 1: bank robbery (دستبرد) --------------------------
    bank_heist_min_level: int = 5
    bank_heist_energy_cost: int = 25
    bank_heist_stake_min: int = 500
    bank_heist_stake_max: int = 100_000
    bank_heist_payout_multiplier: float = 1.6
    bank_heist_base_success: float = 0.35
    bank_heist_min_success: float = 0.10
    bank_heist_max_success: float = 0.60
    bank_heist_size_penalty: float = 0.20
    bank_heist_drip_bonus: float = 0.001
    bank_heist_fine_multiplier: float = 5.0
    bank_heist_jail_seconds: int = 2700
    bank_heist_exp: int = 25
    bank_heist_tool_item: str = "thief_kit"
    bank_heist_tool_bonus: float = 0.20
    cooldown_bank_heist: int = 3600

    # --- Ocean port phase 1: lottery (قرعه) ---------------------------------
    lottery_ticket_price: int = 250
    lottery_pot_share: float = 0.8  # fraction of the ticket price feeding the pot
    lottery_interval_seconds: int = 259_200  # draw every 3 days
    lottery_initial_pot: int = 5_000
    cooldown_lottery: int = 5

    # --- Ocean port phase 1: gift codes (هدیه) -------------------------------
    gift_ttl_days: int = 30
    gift_max_credits: int = 1_000_000
    gift_max_uses: int = 500
    gift_code_min_len: int = 3
    gift_code_max_len: int = 32
    cooldown_gift: int = 3

    # --- Ocean port phase 2: house games (کازینو / قل‌سنگ / شانس / قفل) -----
    # Bet band for every single-player house game (slots, rps, guess, safe).
    rps_house_edge: float = 0.06  # house skims 6% off the fair payout
    rps_win_prob: float = 0.3333333333  # one winning throw out of three
    rps_moves_allowed: int = 3  # rock / paper / scissors
    guess_number_max: int = 20  # the secret is drawn from 1..N
    guess_win_multiplier: float = 19.0  # 1/20 × 19 = 95% return → −5% EV
    safe_code_min: int = 100  # 3-digit lock, never a leading zero
    safe_code_max: int = 999
    safe_max_attempts: int = 6  # attempts before the lock jams shut
    safe_payout_multiplier: float = 6.0  # cracking it pays 6× the escrow
    slot_pair_multiplier: float = 1.5  # any matching pair on the reels
    cooldown_slot: int = 2
    cooldown_rps: int = 3
    cooldown_guess: int = 3
    cooldown_safe_start: int = 3
    cooldown_safe_attempt: int = 5

    # --- Ocean port phase 2: crash round (ریسک) ------------------------------
    risk_growth_rate: float = 0.07  # multiplier gained per second on the wire
    risk_crash_scale: float = 1.6  # exponential scale of the hidden crash point
    risk_crash_floor: float = 1.10  # the wire never snaps below this
    risk_house_edge: float = 0.08  # folded into the crash distribution
    risk_round_seconds: int = 180  # round expiry: a stale stake burns
    cooldown_risk: int = 5

    # --- Ocean port phase 2: two-player arcade (دوز / دروازه) ----------------
    xo_min_bet: int = 10
    xo_max_bet: int = 100_000
    xo_house_rake: float = 0.05  # skim on the pot, like the duel pit
    xo_challenge_seconds: int = 600  # accept window before a challenge voids
    xo_turn_seconds: int = 600  # a stalled board voids and refunds both
    penalty_min_bet: int = 10
    penalty_max_bet: int = 100_000
    penalty_challenge_seconds: int = 600  # accept window before it voids
    penalty_turn_seconds: int = 600  # pick window before the round voids
    cooldown_xo: int = 8
    cooldown_penalty: int = 8

    # --- Ocean port phase 3: black market (کاسب) ----------------------------
    cooldown_blackmarket: int = 3

    # --- Ocean port phase 3: P2P bazaar (بازارچه) ---------------------------
    bazaar_price_min: int = 10
    bazaar_price_max: int = 250_000
    bazaar_max_active: int = 10  # live listings per seller
    cooldown_bazaar: int = 5

    # --- Ocean port phase 3: underworld (سایه / اخاذی) ----------------------
    # سایه هکر — hired hack: fee is burned up-front, the job may still fail.
    shadow_hacker_fee: int = 500
    shadow_hacker_success: float = 0.50
    shadow_hacker_steal_pct: float = 0.08  # of the victim's wallet
    shadow_hacker_steal_min: int = 50
    shadow_hacker_steal_max: int = 5_000
    shadow_hacker_fail_fine: int = 250  # extra penalty when the hack fails
    # سایه قاتل — a contracted jail term for the target.
    shadow_killer_fee: int = 800
    shadow_killer_success: float = 0.40
    shadow_kill_jail_seconds: int = 1800
    cooldown_shadow: int = 900
    # اخاذی — street extortion, no hiring fee, level decides the odds.
    extort_success_base: float = 0.45
    extort_level_bonus: float = 0.02  # per level of advantage over the target
    extort_success_min: float = 0.05
    extort_success_max: float = 0.90
    extort_steal_pct: float = 0.10
    extort_steal_min: int = 50
    extort_steal_max: int = 3_000
    extort_fail_compensation: int = 300  # paid to the victim when it goes wrong
    extort_fail_jail_seconds: int = 600
    cooldown_extort: int = 600
    # Passive item guards (inventory ownership, no equip needed).
    guard_item_id: str = "guard_item"

    # --- Ocean port phase 3: pets (حیوان) -----------------------------------
    pet_base_cost: int = 1_500  # first pet
    pet_upgrade_cost_base: int = 1_200  # level 1 -> 2
    pet_upgrade_cost_growth: float = 1.6  # each further level
    pet_max_level: int = 10
    pet_level_bonus: float = 0.5  # battle score edge per level
    pet_battle_min: int = 100
    pet_battle_max: int = 50_000
    pet_battle_rake: float = 0.10  # house cut of the pot, burned
    pet_challenge_ttl: int = 600  # seconds before a pending duel expires
    cooldown_pet: int = 10
    cooldown_pet_battle: int = 60

    # --- Ocean port phase 4: chat activity → daily bonus --------------------
    chat_reward_per_message: int = 2  # credits per counted group message
    chat_reward_cap: int = 200  # messages counted per daily cycle

    # --- Ocean port phase 4: جام (group cup) --------------------------------
    cup_entry_fee: int = 500
    cup_round_seconds: int = 1800  # entries window per round
    cup_min_players: int = 3  # fewer entries than this cancels + refunds
    cup_rake: float = 0.10  # house cut of the pot, burned
    cup_level_bonus: float = 0.1  # goal-roll edge per level
    cooldown_cup: int = 30
    affair_record_top: int = 5  # rows shown in the خیانت record

    # --- Duel / battle ------------------------------------------------------
    duel_rounds: int = 2
    duel_min_bet: int = 10
    duel_max_bet: int = 100_000
    duel_critical_base: float = 0.08
    duel_critical_drip_bonus: float = 0.005
    duel_critical_multiplier: float = 2.0

    # --- Cooldowns (seconds) ------------------------------------------------
    cooldown_profile: int = 3
    cooldown_duel: int = 8
    cooldown_heist: int = 45
    cooldown_casino: int = 5
    cooldown_shop: int = 5
    cooldown_raid: int = 6

    # --- Pacing -------------------------------------------------------------
    duel_animation_delay: float = 1.1
    raid_attack_delay: float = 0.35

    # --- Tuning helpers -----------------------------------------------------
    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        """Accept ``1,2,3`` / ``1;2;3`` / ``[1,2,3]`` / ``123`` / ``""``."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [int(v) for v in value]
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                parsed = json.loads(text)
                return [int(v) for v in parsed]
            return [
                int(part) for part in text.replace(";", ",").split(",") if part.strip()
            ]
        return value

    @field_validator("required_channels", mode="before")
    @classmethod
    def _parse_required_channels(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    return json.loads(text)
                except Exception:
                    pass
        return value

    @field_validator("db_path", "assets_dir", "manifest_path", "cache_dir", mode="before")
    @classmethod
    def _absolutise(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            path = Path(value)
            return path if path.is_absolute() else ROOT_DIR / path
        return value

    def is_admin(self, user_id: int) -> bool:
        """Whether ``user_id`` holds admin privileges."""
        return user_id in self.admin_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()

# ---------------------------------------------------------------------------
# Phase-1 catalogs.  Pure data (no behaviour): prices and incomes live here
# rather than inside service logic so balance tuning stays in one file.
# ---------------------------------------------------------------------------

# املاک — real estate ladder, prices rise along the list.
PROPERTY_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "کارگاه زیرزمینی",
        "price": 1_200,
        "income": 20,
        "desc": "یه حیاطی زیر خیابان، دستگاه‌ها روشنن و صدای جوش میاد.",
    },
    {
        "name": "بوفه کوچه",
        "price": 3_500,
        "income": 45,
        "desc": "بوفه گوشه کوچه؛ صبح‌ها صف نون و چای داره.",
    },
    {
        "name": "آپارتمان اجاره‌ای",
        "price": 9_000,
        "income": 90,
        "desc": "یه واحد طبقه دوم، مستأجرها مرتب اجاره میزنن.",
    },
    {
        "name": "کافه خیابانی",
        "price": 22_000,
        "income": 180,
        "desc": "کافه پرترافیک وسط خیابان، قهوه‌اش زبانزده.",
    },
    {
        "name": "سالن بدنسازی",
        "price": 55_000,
        "income": 350,
        "desc": "سالن ورزشی طبقه بالا، شب‌ها مربی‌ها شاگرد دارن.",
    },
    {
        "name": "برج تجاری",
        "price": 140_000,
        "income": 700,
        "desc": "برج هفت طبقه با ده‌تا واحد تجاری اجاره‌ای.",
    },
)

# نیرو — hired crew, each pays out on the same tick as properties.
WORKER_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "کارگر ساده",
        "price": 800,
        "income": 15,
        "desc": "کمر خم می‌کنه، کار راه می‌افته.",
    },
    {
        "name": "نگهبان شب",
        "price": 2_500,
        "income": 40,
        "desc": "چراغ‌بهدست، تا صبح پشت در می‌مونه.",
    },
    {
        "name": "حسابدار سایه",
        "price": 7_000,
        "income": 90,
        "desc": "دفترها رو مرتب می‌کنه، هیچ ردی جا نمی‌ذاره.",
    },
    {
        "name": "راننده خصوصی",
        "price": 15_000,
        "income": 160,
        "desc": "همیشه پشت گوشه منتظرته، بار سنگینم جابجا می‌کنه.",
    },
)

# بورس — four tradeable assets. ``base`` anchors the hourly random walk.
MARKET_ASSETS: tuple[dict[str, Any], ...] = (
    {"name": "طلا", "symbol": "GOLD", "base": 1_000},
    {"name": "نفت", "symbol": "OIL", "base": 500},
    {"name": "الماس", "symbol": "DIAMOND", "base": 2_500},
    {"name": "بیت‌کوین", "symbol": "BTC", "base": 5_000},
)

# کازینو — slot machine reels. ``weight`` feeds the weighted draw, ``three``
# is the payout multiplier when all three reels land on the symbol, and any
# matching pair pays ``settings.slot_pair_multiplier``.
SLOT_SYMBOLS: tuple[dict[str, Any], ...] = (
    {"symbol": "🍒", "weight": 42, "three": 12},
    {"symbol": "🗝️", "weight": 28, "three": 20},
    {"symbol": "🎭", "weight": 18, "three": 35},
    {"symbol": "💰", "weight": 9, "three": 70},
    {"symbol": "👑", "weight": 3, "three": 200},
)
