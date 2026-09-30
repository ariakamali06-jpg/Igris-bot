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
from typing import Annotated

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
