-- =============================================================================
-- Urban Fantasy Telegram RPG Bot — SQLite schema
--
-- Design notes:
--   * SQLite has no SELECT ... FOR UPDATE row locking, so cross-table money
--     movement is serialised in application code (database/connection.py wraps
--     every mutation in BEGIN IMMEDIATE under a process-wide asyncio.Lock).
--   * CHECK constraints on every balance column are the last line of defence:
--     even a logic bug cannot write a negative balance.
--   * Timestamps are unix epoch seconds (INTEGER) for cheap comparison.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- Players: identity + progression. One row per Telegram user (global, not
-- per-group, so a player keeps their character across chats).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    user_id            INTEGER PRIMARY KEY,
    username           TEXT,
    display_name       TEXT    NOT NULL,
    level              INTEGER NOT NULL DEFAULT 1    CHECK (level >= 1),
    exp                INTEGER NOT NULL DEFAULT 0    CHECK (exp >= 0),
    energy             INTEGER NOT NULL DEFAULT 100  CHECK (energy >= 0),
    energy_updated_at  INTEGER NOT NULL DEFAULT 0,
    base_atk           INTEGER NOT NULL DEFAULT 10,
    base_def           INTEGER NOT NULL DEFAULT 8,
    base_drip          INTEGER NOT NULL DEFAULT 5,
    last_daily         INTEGER NOT NULL DEFAULT 0,
    created_at         INTEGER NOT NULL DEFAULT 0,
    last_seen          INTEGER NOT NULL DEFAULT 0,
    gender             TEXT    NOT NULL DEFAULT 'نامشخص',
    age                INTEGER NOT NULL DEFAULT 20,
    skin_tone          TEXT    NOT NULL DEFAULT 'fair',
    eye_color          TEXT    NOT NULL DEFAULT 'amber',
    body_stance        TEXT    NOT NULL DEFAULT 'base_street',
    onboarding_completed INTEGER NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------------
-- Wallets: split from players so every balance read/write touches exactly one
-- row, which keeps the critical section as small as possible.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wallets (
    user_id      INTEGER PRIMARY KEY
               REFERENCES players (user_id) ON DELETE CASCADE,
    credits      INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    soul_shards  INTEGER NOT NULL DEFAULT 0 CHECK (soul_shards >= 0)
);

-- -----------------------------------------------------------------------------
-- Item catalog. ``layer_key`` is the PNG basename inside the matching folder
-- under assets/layers/<slot-folder>/, so the catalog and the art stay in sync
-- by construction (see database/items.py, the single source of truth).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS items (
    id                TEXT    PRIMARY KEY,
    name              TEXT    NOT NULL,
    slot              TEXT    NOT NULL
                      CHECK (slot IN ('head', 'body', 'legs',
                                      'weapon', 'accessory', 'aura')),
    rarity            TEXT    NOT NULL
                      CHECK (rarity IN ('common', 'rare', 'epic', 'legendary')),
    atk               INTEGER NOT NULL DEFAULT 0,
    defense           INTEGER NOT NULL DEFAULT 0,
    drip              INTEGER NOT NULL DEFAULT 0,
    price_credits     INTEGER NOT NULL DEFAULT 0 CHECK (price_credits >= 0),
    price_soul_shards INTEGER NOT NULL DEFAULT 0 CHECK (price_soul_shards >= 0),
    layer_key         TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    shop_pool         TEXT    NOT NULL DEFAULT 'rotating'
                      CHECK (shop_pool IN ('rotating', 'permanent', 'starter')),
    UNIQUE (slot, layer_key)
);

-- Which items a player owns.
CREATE TABLE IF NOT EXISTS inventory (
    user_id     INTEGER NOT NULL
                REFERENCES players (user_id) ON DELETE CASCADE,
    item_id     TEXT    NOT NULL
                REFERENCES items (id) ON DELETE CASCADE,
    acquired_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item_id)
);

-- One equipped item per slot. The composite PK enforces "one item per slot"
-- globally instead of relying on application-level flag bookkeeping.
CREATE TABLE IF NOT EXISTS loadout (
    user_id  INTEGER NOT NULL
             REFERENCES players (user_id) ON DELETE CASCADE,
    slot     TEXT    NOT NULL
             CHECK (slot IN ('head', 'body', 'legs',
                             'weapon', 'accessory', 'aura')),
    item_id  TEXT    NOT NULL
             REFERENCES items (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, slot),
    FOREIGN KEY (user_id, item_id) REFERENCES inventory (user_id, item_id)
        ON DELETE CASCADE
);

-- -----------------------------------------------------------------------------
-- Duels. ``seed`` makes the outcome reproducible: the battle engine is a pure
-- function of (stats, seed), so any dispute can be re-simulated offline.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS duels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL,
    opponent_id   INTEGER NOT NULL,
    stake         INTEGER NOT NULL DEFAULT 0 CHECK (stake >= 0),
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'declined', 'expired',
                                    'cancelled', 'resolved', 'forfeit')),
    winner_id     INTEGER,
    seed          INTEGER NOT NULL,
    log_json      TEXT    NOT NULL DEFAULT '[]',
    created_at    INTEGER NOT NULL DEFAULT 0,
    resolved_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_duels_chat_status ON duels (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_duels_created     ON duels (created_at);

-- -----------------------------------------------------------------------------
-- Raids: a single boss HP pool shared by everyone in the group.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raids (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          INTEGER NOT NULL,
    boss_name        TEXT    NOT NULL,
    boss_key         TEXT    NOT NULL,
    hp               INTEGER NOT NULL CHECK (hp >= 0),
    max_hp           INTEGER NOT NULL CHECK (max_hp > 0),
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'cleared', 'expired')),
    chat_message_id  INTEGER,
    spawned_at       INTEGER NOT NULL DEFAULT 0,
    cleared_at       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_raids_chat_status ON raids (chat_id, status);

CREATE TABLE IF NOT EXISTS raid_participants (
    raid_id      INTEGER NOT NULL
                 REFERENCES raids (id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    damage       INTEGER NOT NULL DEFAULT 0 CHECK (damage >= 0),
    hits         INTEGER NOT NULL DEFAULT 0 CHECK (hits >= 0),
    claimed      INTEGER NOT NULL DEFAULT 0,
    last_hit_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (raid_id, user_id)
);

CREATE TABLE IF NOT EXISTS raid_loot (
    raid_id      INTEGER NOT NULL
                 REFERENCES raids (id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    credits      INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    soul_shards  INTEGER NOT NULL DEFAULT 0 CHECK (soul_shards >= 0),
    PRIMARY KEY (raid_id, user_id)
);

-- Per-group chat activity counter driving raid spawns (persists restarts).
CREATE TABLE IF NOT EXISTS group_state (
    chat_id               INTEGER PRIMARY KEY,
    message_count         INTEGER NOT NULL DEFAULT 0,
    messages_to_next_raid INTEGER NOT NULL DEFAULT 0,
    active_raid_id        INTEGER,
    last_raid_at          INTEGER NOT NULL DEFAULT 0,
    last_bonus_at         INTEGER NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------------
-- Ledger: append-only audit trail. Every credit/shard mutation writes exactly
-- one row, referencing the business event in ``ref`` (e.g. "duel:42").
-- Balance-after columns make reconstruction cheap without replaying deltas.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ledger (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    kind                 TEXT    NOT NULL,
    credits_delta        INTEGER NOT NULL DEFAULT 0,
    shards_delta         INTEGER NOT NULL DEFAULT 0,
    balance_after_credits INTEGER NOT NULL DEFAULT 0,
    balance_after_shards INTEGER NOT NULL DEFAULT 0,
    ref                  TEXT    NOT NULL DEFAULT '',
    created_at           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_ref  ON ledger (ref);

-- Purchase history (feeds "recent drops" / auditing).
CREATE TABLE IF NOT EXISTS purchases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    item_id       TEXT    NOT NULL,
    price_credits INTEGER NOT NULL DEFAULT 0,
    price_shards  INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases (user_id, created_at);

-- Deterministic daily rotating shop stock.
CREATE TABLE IF NOT EXISTS shop_rotation (
    rotation_key TEXT NOT NULL,
    slot         INTEGER NOT NULL,
    item_id      TEXT    NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    PRIMARY KEY (rotation_key, slot)
);

-- The canonical slot list, kept in the DB so it can be validated once at boot.
CREATE TABLE IF NOT EXISTS constants (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO constants (key, value) VALUES
    ('schema_version', '1'),
    ('slots', 'head,body,legs,weapon,accessory,aura'),
    ('rarities', 'common,rare,epic,legendary');
