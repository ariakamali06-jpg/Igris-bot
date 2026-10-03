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
    hair_color         TEXT    NOT NULL DEFAULT 'black',
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    education_level    INTEGER NOT NULL DEFAULT 0,
    job                TEXT    NOT NULL DEFAULT 'بیکار',
    spouse_id          INTEGER DEFAULT NULL,
    marriage_date      INTEGER NOT NULL DEFAULT 0,
    children_count     INTEGER NOT NULL DEFAULT 0,
    is_pregnant_until  INTEGER NOT NULL DEFAULT 0,
    is_jailed_until    INTEGER NOT NULL DEFAULT 0,
    shield_until       INTEGER NOT NULL DEFAULT 0,
    pet_level          INTEGER NOT NULL DEFAULT 0,
    affair_count       INTEGER NOT NULL DEFAULT 0,
    bank_balance       INTEGER NOT NULL DEFAULT 0,
    loan_amount        INTEGER NOT NULL DEFAULT 0,
    loan_due           INTEGER NOT NULL DEFAULT 0,
    last_work_time     INTEGER NOT NULL DEFAULT 0,
    last_study_time    INTEGER NOT NULL DEFAULT 0,
    last_steal_time    INTEGER NOT NULL DEFAULT 0,
    last_duel_time     INTEGER NOT NULL DEFAULT 0,
    last_intimacy_time INTEGER NOT NULL DEFAULT 0,
    last_affair_time   INTEGER NOT NULL DEFAULT 0,
    eye_style          TEXT    NOT NULL DEFAULT 'eyes1_1',
    mouth_style        TEXT    NOT NULL DEFAULT 'mouth1_1',
    hair_style         TEXT    NOT NULL DEFAULT 'hair1',
    clan_id            INTEGER DEFAULT NULL,
    clan_role          TEXT    DEFAULT NULL
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
                      CHECK (shop_pool IN ('rotating', 'permanent',
                                           'starter', 'blackmarket')),
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

-- -----------------------------------------------------------------------------
-- Clashing and City Clans
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clans (
    clan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL DEFAULT 0,
    name        TEXT    NOT NULL UNIQUE,
    leader_id   INTEGER NOT NULL REFERENCES players(user_id),
    treasury    INTEGER NOT NULL DEFAULT 0 CHECK (treasury >= 0),
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- -----------------------------------------------------------------------------
-- Ocean port phase 1 — passive economy: properties (املاک) and hired crew (نیرو).
-- One row per owned asset; income accrues once per property_income_interval.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS properties (
    owner_id     INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL CHECK (kind IN ('property', 'worker')),
    name         TEXT    NOT NULL,
    level        INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
    last_collect INTEGER NOT NULL DEFAULT 0,
    acquired_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, kind, name)
);

-- Daily contracts (قرارداد). ``day`` is the contract-window index so a new
-- day simply starts a fresh snapshot; progress is recounted from the ledger.
CREATE TABLE IF NOT EXISTS contracts (
    user_id  INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    code     TEXT    NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0),
    day      INTEGER NOT NULL DEFAULT 0,
    done     INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    PRIMARY KEY (user_id, code)
);

-- Exchange (بورس): hourly deterministic price series + player positions.
CREATE TABLE IF NOT EXISTS market_prices (
    asset TEXT    NOT NULL,
    hour  INTEGER NOT NULL,
    price REAL    NOT NULL CHECK (price > 0),
    PRIMARY KEY (asset, hour)
);

CREATE TABLE IF NOT EXISTS holdings (
    user_id    INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    asset      TEXT    NOT NULL,
    units      REAL    NOT NULL DEFAULT 0 CHECK (units >= 0),
    updated_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, asset)
);

CREATE TABLE IF NOT EXISTS market_bets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    hour       INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'won', 'lost', 'push')),
    payout     INTEGER NOT NULL DEFAULT 0 CHECK (payout >= 0),
    created_at INTEGER NOT NULL DEFAULT 0,
    settled_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_market_bets_user ON market_bets (user_id, status);

-- Lottery (قرعه): single-row pot state plus per-round ticket counts.
CREATE TABLE IF NOT EXISTS lottery_state (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    pot            INTEGER NOT NULL DEFAULT 0 CHECK (pot >= 0),
    next_draw_at   INTEGER NOT NULL DEFAULT 0,
    round          INTEGER NOT NULL DEFAULT 0,
    last_winner_id INTEGER,
    last_draw_at   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lottery_tickets (
    user_id INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    round   INTEGER NOT NULL,
    tickets INTEGER NOT NULL DEFAULT 0 CHECK (tickets >= 0),
    PRIMARY KEY (user_id, round)
);

-- Gift codes (هدیه): one row per code, plus per-user redemptions so a code
-- can never be claimed twice by the same player.
CREATE TABLE IF NOT EXISTS gift_codes (
    code       TEXT    PRIMARY KEY,
    credits    INTEGER NOT NULL CHECK (credits >= 0),
    max_uses   INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    used       INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
    expires_at INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gift_redemptions (
    code       TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, user_id)
);

-- -----------------------------------------------------------------------------
-- Ocean port phase 2 — crash round (ریسک). The stake is escrowed at start;
-- ``crash_point`` is the hidden multiplier where the wire snaps, and
-- ``started_at`` makes the live multiplier a pure function of elapsed time.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_rounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    chat_id     INTEGER NOT NULL DEFAULT 0,
    stake       INTEGER NOT NULL CHECK (stake >= 0),
    crash_point REAL    NOT NULL CHECK (crash_point > 0),
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'cashed', 'crashed', 'expired')),
    payout      INTEGER NOT NULL DEFAULT 0 CHECK (payout >= 0),
    started_at  INTEGER NOT NULL DEFAULT 0,
    resolved_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_risk_rounds_user ON risk_rounds (user_id, status);

-- 3-digit safe (قفل): one live lock per player, attempts counted until the
-- lock opens (payout) or jams (stake forfeited). The code never leaves the row.
CREATE TABLE IF NOT EXISTS safe_locks (
    user_id     INTEGER PRIMARY KEY REFERENCES players (user_id) ON DELETE CASCADE,
    stake       INTEGER NOT NULL CHECK (stake >= 0),
    code        TEXT    NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'opened', 'failed')),
    payout      INTEGER NOT NULL DEFAULT 0 CHECK (payout >= 0),
    created_at  INTEGER NOT NULL DEFAULT 0,
    resolved_at INTEGER
);

-- Tic-tac-toe (دوز): ``board`` is 9 characters ('.', 'X', 'O') read left to
-- right, top to bottom; both stakes are escrowed before the first mark lands.
CREATE TABLE IF NOT EXISTS xo_games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    opponent_id   INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    stake         INTEGER NOT NULL DEFAULT 0 CHECK (stake >= 0),
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'active', 'declined', 'expired',
                                    'cancelled', 'resolved')),
    board         TEXT    NOT NULL DEFAULT '.........',
    turn_id       INTEGER,
    winner_id     INTEGER,
    created_at    INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0,
    resolved_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_xo_games_chat_status ON xo_games (chat_id, status);

-- Penalty (دروازه): the keeper commits a hidden save first, then the shooter
-- picks a shot; both stay NULL until their owner presses a button.
CREATE TABLE IF NOT EXISTS penalty_rounds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    shooter_id   INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    keeper_id    INTEGER NOT NULL REFERENCES players (user_id) ON DELETE CASCADE,
    stake        INTEGER NOT NULL DEFAULT 0 CHECK (stake >= 0),
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'keeper_pick', 'shooter_pick',
                                   'declined', 'expired', 'cancelled',
                                   'resolved')),
    keeper_pick  TEXT,
    shooter_pick TEXT,
    winner_id    INTEGER,
    created_at   INTEGER NOT NULL DEFAULT 0,
    updated_at   INTEGER NOT NULL DEFAULT 0,
    resolved_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_penalty_chat_status ON penalty_rounds (chat_id, status);

-- ---------------------------------------------------------------------------
-- Ocean port phase 3 — بازارچه (player-to-player listings).
-- The item row is ESCROWED out of ``inventory`` while a listing is active
-- and only lands in the buyer's inventory when the sale resolves, so a
-- listing can never sell something that was also sold elsewhere.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bazaar_listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id   INTEGER NOT NULL
                REFERENCES players (user_id) ON DELETE CASCADE,
    buyer_id    INTEGER
                REFERENCES players (user_id) ON DELETE SET NULL,
    item_id     TEXT    NOT NULL
                REFERENCES items (id) ON DELETE CASCADE,
    price       INTEGER NOT NULL CHECK (price > 0),
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'sold', 'cancelled')),
    created_at  INTEGER NOT NULL DEFAULT 0,
    resolved_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_bazaar_status ON bazaar_listings (status);

-- ---------------------------------------------------------------------------
-- Ocean port phase 3 — حیوان نبرد (pet duels).
-- The challenger's stake is ESCROWED at creation (ledger ref ``pet:stake``)
-- so a fight can always be settled or refunded inside one db.write() txn.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pet_challenges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    challenger_id INTEGER NOT NULL
                  REFERENCES players (user_id) ON DELETE CASCADE,
    target_id     INTEGER NOT NULL
                  REFERENCES players (user_id) ON DELETE CASCADE,
    amount        INTEGER NOT NULL CHECK (amount > 0),
    chat_id       INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'resolved', 'declined',
                                    'expired', 'cancelled')),
    winner_id     INTEGER,
    created_at    INTEGER NOT NULL DEFAULT 0,
    expires_at    INTEGER NOT NULL DEFAULT 0,
    resolved_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pet_challenges_pair
    ON pet_challenges (challenger_id, target_id, status);

-- ---------------------------------------------------------------------------
-- Ocean port phase 4 — chat activity counted toward the next روزانه claim.
-- Bumped by the group-activity middleware; reset to 0 when paid out.
-- No FK to players on purpose: lurkers may count before /start.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_activity (
    user_id    INTEGER PRIMARY KEY,
    messages   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- Ocean port phase 4 — جام (group cup rounds). Entry fees are escrowed on
-- join and only leave the wallet as prizes (cup:win) or refunds
-- (cup:refund), so an under-attended round can always pay everyone back.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cup_rounds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'open'
               CHECK (status IN ('open', 'settled', 'cancelled')),
    entry_fee  INTEGER NOT NULL CHECK (entry_fee > 0),
    created_at INTEGER NOT NULL DEFAULT 0,
    closes_at  INTEGER NOT NULL DEFAULT 0,
    settled_at INTEGER
);

CREATE TABLE IF NOT EXISTS cup_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id   INTEGER NOT NULL
               REFERENCES cup_rounds (id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL
               REFERENCES players (user_id) ON DELETE CASCADE,
    goals      INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE (round_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_cup_rounds_chat
    ON cup_rounds (chat_id, status);
