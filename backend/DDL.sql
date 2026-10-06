-- Émile Platform — Database DDL Schema (PostgreSQL 15+)

-- Drop existing tables/types if needed
DROP TABLE IF EXISTS lore_moderation_log CASCADE;
DROP TABLE IF EXISTS ingest_log CASCADE;
DROP TABLE IF EXISTS model_runs CASCADE;
DROP TABLE IF EXISTS daily_universe CASCADE;
DROP TABLE IF EXISTS tokens CASCADE;
DROP TYPE IF EXISTS token_status CASCADE;

-- Token status enum
CREATE TYPE token_status AS ENUM ('pending', 'passed', 'stalled', 'excluded');

-- Tokens table: All pump.fun tokens clearing peak market cap $10,000
CREATE TABLE tokens (
    mint                TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    lore                TEXT,                             -- Raw metadata description
    lore_display        TEXT,                             -- Sanitized display lore (max 280 chars, URLs & control chars removed)
    lore_withheld       BOOLEAN NOT NULL DEFAULT FALSE,   -- True if profanity/slur filter triggered
    image_url           TEXT,                             -- Original Metaplex/IPFS image URI
    image_cached_path   TEXT,                             -- Local 64x64 WebP thumbnail path
    creator             TEXT,
    launched_at         TIMESTAMPTZ NOT NULL,
    launch_hour_utc     SMALLINT GENERATED ALWAYS AS 
                        (EXTRACT(HOUR FROM launched_at AT TIME ZONE 'UTC')::SMALLINT) STORED,

    peak_mc             NUMERIC(20,2) NOT NULL DEFAULT 0,
    last_seen_mc        NUMERIC(20,2),
    crossed_10k_at      TIMESTAMPTZ,                      -- Timestamp when token entered study
    holders             INTEGER,                          -- Holder count sampled at 48h mark
    holders_sampled_at  TIMESTAMPTZ,

    status              token_status NOT NULL DEFAULT 'pending',
    labeled_at          TIMESTAMPTZ,

    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_polled_at      TIMESTAMPTZ,
    poll_count          INTEGER NOT NULL DEFAULT 0
);

-- Indexes for performance optimization
CREATE INDEX idx_tokens_status_launch ON tokens (status, launched_at DESC);
CREATE INDEX idx_tokens_pending_poll  ON tokens (last_polled_at) WHERE status = 'pending';
CREATE INDEX idx_tokens_crossed       ON tokens (crossed_10k_at DESC) WHERE crossed_10k_at IS NOT NULL;

-- Daily Universe table: Aggregate daily counters for true base rate calculation
CREATE TABLE daily_universe (
    day                DATE PRIMARY KEY,
    minted_total       INTEGER NOT NULL DEFAULT 0,
    crossed_10k        INTEGER NOT NULL DEFAULT 0,
    crossed_30k        INTEGER NOT NULL DEFAULT 0
);

-- Model Runs table: Hourly training run history & Jar level calculation
CREATE TABLE model_runs (
    id              BIGSERIAL PRIMARY KEY,
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    n_samples       INTEGER NOT NULL,
    n_positive      INTEGER NOT NULL,
    capacity_d      INTEGER NOT NULL,
    auc_mean        DOUBLE PRECISION NOT NULL,
    auc_std         DOUBLE PRECISION NOT NULL,
    epsilon_vc      DOUBLE PRECISION NOT NULL,
    auc_boot_lower  DOUBLE PRECISION NOT NULL,
    proven_floor    DOUBLE PRECISION NOT NULL,
    jar_level       DOUBLE PRECISION NOT NULL,            -- Clamped 0.0 .. 1.0
    gates_status    JSONB NOT NULL,                       -- {n_samples, n_positive, auc_std, time_split}
    blocked_by      TEXT,                                 -- First failing gate name, NULL if all passed
    feature_importance JSONB NOT NULL,
    hour_rates      JSONB NOT NULL,
    notes           TEXT                                  -- Includes failed training attempts
);

-- Ingest Log table: Worker performance and latency tracking
CREATE TABLE ingest_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT NOT NULL,                            -- 'pumpfun' | 'dexscreener' | 'rpc'
    ok          INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    latency_ms  INTEGER
);

-- Lore Moderation Log table: Audit trail for lore sanitization
CREATE TABLE lore_moderation_log (
    mint            TEXT PRIMARY KEY REFERENCES tokens(mint),
    raw_lore        TEXT,
    filtered_reason TEXT,                                 -- 'profanity' | 'slur' | 'url_stripped' | 'control_chars' | NULL
    filtered_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Twitter Posts table: Audit trail for auto-tweet publications
CREATE TABLE IF NOT EXISTS twitter_posts (
    id              BIGSERIAL PRIMARY KEY,
    tweet_id        TEXT,
    text            TEXT NOT NULL,
    target_token    TEXT NOT NULL DEFAULT 'emile',
    market_cap_usd  NUMERIC(20, 2),
    volume_24h_usd  NUMERIC(20, 2),
    holders_count   INTEGER,
    trigger_type    TEXT NOT NULL DEFAULT 'recurring_2h_news',
    status          TEXT NOT NULL DEFAULT 'dry_run',
    posted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_message   TEXT
);


-- ===========================================================================
-- Epochs page (applied idempotently by app/db/epochs_schema.py at watcher startup)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS epochs (
    id            SMALLINT PRIMARY KEY CHECK (id BETWEEN 1 AND 6),
    key           TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'locked' CHECK (status IN ('locked', 'complete')),
    proof_json    JSONB,
    completed_at  TIMESTAMPTZ,
    CHECK (status <> 'complete' OR (proof_json IS NOT NULL AND completed_at IS NOT NULL))
);

INSERT INTO epochs (id, key) VALUES
    (1, 'ingestion'), (2, 'hourglass_fill'), (3, 'first_trade'),
    (4, 'first_burn'), (5, 'golem_launch'), (6, 'open_golem')
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION epochs_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'epochs rows cannot be deleted';
    END IF;
    IF OLD.status = 'complete' THEN
        RAISE EXCEPTION 'epoch % is complete; completion is permanent', OLD.id;
    END IF;
    IF NEW.status = 'complete' AND NEW.id > 1 AND NOT EXISTS (
        SELECT 1 FROM epochs WHERE id = NEW.id - 1 AND status = 'complete'
    ) THEN
        RAISE EXCEPTION 'epoch % cannot complete before epoch %', NEW.id, NEW.id - 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_epochs_guard ON epochs;

CREATE TRIGGER trg_epochs_guard
BEFORE UPDATE OR DELETE ON epochs
FOR EACH ROW EXECUTE FUNCTION epochs_guard();

CREATE TABLE IF NOT EXISTS chain_cursor (
    name        TEXT PRIMARY KEY,
    last_block  BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS golem_swaps (
    tx_hash  TEXT PRIMARY KEY,
    block    BIGINT NOT NULL,
    at       TIMESTAMPTZ NOT NULL
);

ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS side TEXT;

ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS token TEXT;

ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS token_amount_wei NUMERIC(78, 0);

ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS eth_amount_wei NUMERIC(78, 0);

CREATE TABLE IF NOT EXISTS golem_trade_decisions (
    id                    BIGSERIAL PRIMARY KEY,
    decided_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    token                 TEXT NOT NULL,
    side                  TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    survival_probability  DOUBLE PRECISION NOT NULL CHECK (survival_probability BETWEEN 0 AND 1),
    top_signal            TEXT NOT NULL,
    run_id                BIGINT NOT NULL REFERENCES model_runs(id),
    tx_hash               TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS epc_burns (
    tx_hash       TEXT NOT NULL,
    log_index     INTEGER NOT NULL,
    block         BIGINT NOT NULL,
    at            TIMESTAMPTZ NOT NULL,
    burn_address  TEXT NOT NULL,
    amount_wei    NUMERIC(78, 0) NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS launcher_events (
    tx_hash    TEXT NOT NULL,
    log_index  INTEGER NOT NULL,
    block      BIGINT NOT NULL,
    at         TIMESTAMPTZ NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('launched', 'agent_updated')),
    token      TEXT,
    pair       TEXT,
    new_agent  TEXT,
    PRIMARY KEY (tx_hash, log_index)
);

-- Model artifacts (app/db/model_artifacts_schema.py): the exact model behind each run, for live scoring
CREATE TABLE IF NOT EXISTS model_artifacts (
    run_id      BIGINT PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256      TEXT NOT NULL,                        -- sha256 of the canonical JSON in `artifact`
    artifact    TEXT NOT NULL                         -- {format, feature_names, embedder, pca, booster}
);

CREATE OR REPLACE FUNCTION model_artifacts_guard() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'model_artifacts rows are immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS model_artifacts_immutable ON model_artifacts;

CREATE TRIGGER model_artifacts_immutable BEFORE UPDATE ON model_artifacts
FOR EACH ROW EXECUTE FUNCTION model_artifacts_guard();

-- The Desk (app/db/desk_schema.py)

CREATE TABLE IF NOT EXISTS desk_scores (
    mint         TEXT PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
    survival     DOUBLE PRECISION NOT NULL CHECK (survival BETWEEN 0 AND 1),
    top_signals  JSONB NOT NULL,
    scored_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS desk_candidates (
    id              BIGSERIAL PRIMARY KEY,
    token           TEXT NOT NULL,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    survival        DOUBLE PRECISION NOT NULL CHECK (survival BETWEEN 0 AND 1),
    stage           TEXT NOT NULL DEFAULT 'liquidity_check'
                    CHECK (stage IN ('liquidity_check', 'sizing', 'entering', 'open', 'dropped')),
    dropped_reason  TEXT,
    dropped_at      TIMESTAMPTZ,
    decision_id     BIGINT REFERENCES golem_trade_decisions(id),
    CHECK (stage <> 'dropped' OR (dropped_reason IS NOT NULL AND dropped_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS desk_state (
    id                INT PRIMARY KEY CHECK (id = 1),
    state             TEXT NOT NULL,
    changed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_decision_at  TIMESTAMPTZ
);

INSERT INTO desk_state (id, state) VALUES (1, 'gated') ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS desk_announcements (
    trade_id      TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('open', 'close')),
    tx_hash       TEXT NOT NULL,
    announced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_id, kind)
);

ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_canonical TEXT;

ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_sha256 TEXT;

ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS exit_reason TEXT
    CHECK (exit_reason IN ('take_profit', 'stop_loss', 'max_hold'));

CREATE OR REPLACE FUNCTION golem_trade_decisions_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'golem_trade_decisions rows cannot be deleted';
    END IF;
    IF OLD.tx_hash IS NOT NULL AND NEW.tx_hash IS DISTINCT FROM OLD.tx_hash THEN
        RAISE EXCEPTION 'tx_hash is already set';
    END IF;
    IF (NEW.decided_at, NEW.token, NEW.side, NEW.survival_probability, NEW.top_signal, NEW.run_id)
           IS DISTINCT FROM
       (OLD.decided_at, OLD.token, OLD.side, OLD.survival_probability, OLD.top_signal, OLD.run_id)
       OR NEW.why_canonical IS DISTINCT FROM OLD.why_canonical
       OR NEW.why_sha256 IS DISTINCT FROM OLD.why_sha256
       OR NEW.exit_reason IS DISTINCT FROM OLD.exit_reason THEN
        RAISE EXCEPTION 'golem_trade_decisions rows are append-only';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS golem_trade_decisions_append_only ON golem_trade_decisions;

CREATE TRIGGER golem_trade_decisions_append_only BEFORE UPDATE OR DELETE ON golem_trade_decisions
FOR EACH ROW EXECUTE FUNCTION golem_trade_decisions_guard();

-- GoForge (app/db/goforge_schema.py)
CREATE TABLE IF NOT EXISTS goforge_launches (
    id              TEXT PRIMARY KEY,
    ca              TEXT NOT NULL UNIQUE,
    name            TEXT,
    symbol          TEXT,
    launch_tx       TEXT NOT NULL,
    launched_at     TIMESTAMPTZ,
    peak_mc_usd     NUMERIC DEFAULT 0,
    verdict         TEXT CHECK (verdict IN ('pending','reached_30k','stalled')) DEFAULT 'pending',
    verdict_at      TIMESTAMPTZ,
    fees_usd        NUMERIC DEFAULT 0,
    epc_burned      NUMERIC DEFAULT 0,
    updated_at      TIMESTAMPTZ,
    pool_active_at  TIMESTAMPTZ,
    pair_url        TEXT,
    decimals        INTEGER,
    total_supply    NUMERIC,
    deployer        TEXT,
    top10_pct       DOUBLE PRECISION,
    top10_at        TIMESTAMPTZ,
    last_source_ok_at TIMESTAMPTZ,
    epc_burned_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS goforge_snapshots (
    launch_id       TEXT REFERENCES goforge_launches(id),
    ts              TIMESTAMPTZ NOT NULL,
    price_usd       NUMERIC,
    mc_usd          NUMERIC,
    liquidity_usd   NUMERIC,
    volume_24h_usd  NUMERIC,
    holders         INTEGER,
    PRIMARY KEY (launch_id, ts)
);

-- Rows are never deleted, a locked verdict is final, identity columns never change once set
CREATE OR REPLACE FUNCTION goforge_launches_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'goforge_launches rows cannot be deleted';
    END IF;
    IF OLD.verdict <> 'pending' AND (NEW.verdict IS DISTINCT FROM OLD.verdict
                                     OR NEW.verdict_at IS DISTINCT FROM OLD.verdict_at
                                     OR NEW.peak_mc_usd IS DISTINCT FROM OLD.peak_mc_usd) THEN
        RAISE EXCEPTION 'goforge verdict is locked';
    END IF;
    IF NEW.ca IS DISTINCT FROM OLD.ca OR NEW.launch_tx IS DISTINCT FROM OLD.launch_tx
       OR (OLD.launched_at IS NOT NULL AND NEW.launched_at IS DISTINCT FROM OLD.launched_at)
       OR (OLD.pool_active_at IS NOT NULL AND NEW.pool_active_at IS DISTINCT FROM OLD.pool_active_at) THEN
        RAISE EXCEPTION 'goforge launch identity is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS goforge_launches_immutable ON goforge_launches;

CREATE TRIGGER goforge_launches_immutable BEFORE UPDATE OR DELETE ON goforge_launches
FOR EACH ROW EXECUTE FUNCTION goforge_launches_guard();

-- Meta Radar (app/db/radar_schema.py)
CREATE TABLE IF NOT EXISTS radar_runs (
    run_id          TEXT PRIMARY KEY,
    run_at          TIMESTAMPTZ NOT NULL,
    n_tokens        INTEGER,
    n_resolved      INTEGER,
    baseline_rate   NUMERIC,
    method          TEXT,
    params_json     JSONB
);

CREATE TABLE IF NOT EXISTS radar_clusters (
    run_id          TEXT REFERENCES radar_runs(run_id),
    cluster_id      TEXT,
    label           TEXT,
    keywords        TEXT[],
    label_source    TEXT,
    n_total         INTEGER,
    n_resolved      INTEGER,
    reached_30k     INTEGER,
    survival_rate   NUMERIC,
    ci_low          NUMERIC,
    ci_high         NUMERIC,
    lift            NUMERIC,
    median_peak_mc  NUMERIC,
    launches_7d     INTEGER,
    trend_pct       NUMERIC,
    share_7d        NUMERIC,
    survival_7d     NUMERIC,
    survival_30d    NUMERIC,
    saturation      BOOLEAN,
    centroid_x      NUMERIC,
    centroid_y      NUMERIC,
    PRIMARY KEY (run_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS radar_points (
    run_id          TEXT REFERENCES radar_runs(run_id),
    token_address   TEXT,
    cluster_id      TEXT,
    x               NUMERIC,
    y               NUMERIC,
    resolved        BOOLEAN,
    reached_30k     BOOLEAN,
    launched_at     TIMESTAMPTZ,
    PRIMARY KEY (run_id, token_address)
);

ALTER TABLE radar_clusters ADD COLUMN IF NOT EXISTS centroid_vec DOUBLE PRECISION[];

CREATE INDEX IF NOT EXISTS idx_radar_points_cluster ON radar_points (run_id, cluster_id);

-- GoForge Registry (app/db/gf_schema.py)
CREATE TABLE IF NOT EXISTS gf_creators (
    x_user_id         TEXT PRIMARY KEY,
    x_handle          TEXT,
    x_verified        BOOLEAN,
    x_created_at      TIMESTAMPTZ,
    x_followers       INTEGER,
    wallet            TEXT UNIQUE,
    last_win_at       TIMESTAMPTZ,
    wins_reached_30k  INTEGER DEFAULT 0,
    wins_stalled      INTEGER DEFAULT 0,
    updated_at        TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS gf_rounds (
    round_date        DATE PRIMARY KEY,
    status            TEXT CHECK (status IN ('submit','review','vote','scoring','announced','launched','no_launch')),
    winner_idea_id    TEXT,
    n_ideas           INTEGER,
    n_votes           INTEGER,
    announced_at      TIMESTAMPTZ,
    no_launch_reason  TEXT
);
ALTER TABLE gf_rounds ADD COLUMN IF NOT EXISTS votes_closed_at TIMESTAMPTZ;
ALTER TABLE gf_rounds ADD COLUMN IF NOT EXISTS scoreboard_sha256 TEXT;
CREATE TABLE IF NOT EXISTS gf_ideas (
    idea_id           TEXT PRIMARY KEY,
    round_date        DATE REFERENCES gf_rounds(round_date),
    x_user_id         TEXT REFERENCES gf_creators(x_user_id),
    wallet            TEXT NOT NULL,
    name              TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    lore              TEXT NOT NULL,
    image_url         TEXT NOT NULL,
    image_sha256      TEXT NOT NULL,
    fee_tx            TEXT UNIQUE NOT NULL,
    status            TEXT CHECK (status IN ('pending_review','approved','rejected')),
    reject_reason     TEXT,
    radar_cluster_id  TEXT,
    score_credibility NUMERIC,
    score_golem       NUMERIC,
    score_vote        NUMERIC,
    score_final       NUMERIC,
    submitted_at      TIMESTAMPTZ NOT NULL
);
ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS auto_flags JSONB;
ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS votes_counted INTEGER;
ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS lore_embedding DOUBLE PRECISION[];
CREATE INDEX IF NOT EXISTS idx_gf_ideas_round ON gf_ideas (round_date, status);
CREATE INDEX IF NOT EXISTS idx_gf_ideas_wallet ON gf_ideas (wallet, round_date);
CREATE TABLE IF NOT EXISTS gf_votes (
    round_date        DATE,
    voter_wallet      TEXT,
    idea_id           TEXT REFERENCES gf_ideas(idea_id),
    signature         TEXT NOT NULL,
    epc_balance_vote  NUMERIC,
    epc_balance_close NUMERIC,
    counted           BOOLEAN,
    voted_at          TIMESTAMPTZ,
    PRIMARY KEY (round_date, voter_wallet)
);
ALTER TABLE gf_votes ADD COLUMN IF NOT EXISTS signed_at BIGINT;
CREATE TABLE IF NOT EXISTS gf_vote_events (
    id            BIGSERIAL PRIMARY KEY,
    voter_wallet  TEXT NOT NULL,
    round_date    DATE NOT NULL,
    idea_id       TEXT NOT NULL,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gf_vote_events_wallet ON gf_vote_events (voter_wallet, at);
CREATE TABLE IF NOT EXISTS gf_launches (
    idea_id           TEXT PRIMARY KEY REFERENCES gf_ideas(idea_id),
    ca                TEXT UNIQUE,
    launch_tx         TEXT,
    launched_at       TIMESTAMPTZ,
    splitter_address  TEXT,
    fees_to_creator   NUMERIC DEFAULT 0,
    epc_burned        NUMERIC DEFAULT 0,
    peak_mc_usd       NUMERIC DEFAULT 0,
    verdict           TEXT CHECK (verdict IN ('pending','reached_30k','stalled')) DEFAULT 'pending',
    verdict_at        TIMESTAMPTZ
);
ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS launch_due_at TIMESTAMPTZ;
ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS launch_hour_reason TEXT;
ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS last_distribute_at TIMESTAMPTZ;
ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS dist_block BIGINT;
ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS verdict_synced BOOLEAN DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS gf_distributions (
    tx_hash         TEXT NOT NULL,
    log_index       INTEGER NOT NULL,
    idea_id         TEXT NOT NULL,
    block           BIGINT NOT NULL,
    at              TIMESTAMPTZ NOT NULL,
    creator_wei     NUMERIC(78, 0) NOT NULL,
    burn_wei        NUMERIC(78, 0) NOT NULL,
    epc_burned_wei  NUMERIC(78, 0) NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);
CREATE TABLE IF NOT EXISTS gf_posts (
    kind        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending','posted','dry_run','failed')),
    tweet_id    TEXT,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    posted_at   TIMESTAMPTZ,
    PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS gf_nonces (
    nonce       TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at     TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS gf_oauth_states (
    state          TEXT PRIMARY KEY,
    wallet         TEXT NOT NULL,
    code_verifier  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS gf_wallet_changes (
    id           BIGSERIAL PRIMARY KEY,
    x_user_id    TEXT NOT NULL,
    old_wallet   TEXT,
    new_wallet   TEXT,
    reason       TEXT NOT NULL,
    changed_by   TEXT NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION gf_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'gf_ideas' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'gf_ideas rows cannot be deleted';
        END IF;
        IF NEW.name IS DISTINCT FROM OLD.name OR NEW.ticker IS DISTINCT FROM OLD.ticker
           OR NEW.lore IS DISTINCT FROM OLD.lore OR NEW.image_sha256 IS DISTINCT FROM OLD.image_sha256
           OR NEW.wallet IS DISTINCT FROM OLD.wallet OR NEW.fee_tx IS DISTINCT FROM OLD.fee_tx THEN
            RAISE EXCEPTION 'a submitted idea cannot be edited';
        END IF;
    ELSIF TG_TABLE_NAME = 'gf_rounds' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'gf_rounds rows cannot be deleted';
        END IF;
        IF OLD.winner_idea_id IS NOT NULL AND NEW.winner_idea_id IS DISTINCT FROM OLD.winner_idea_id THEN
            RAISE EXCEPTION 'the winner of a round is final';
        END IF;
    ELSIF TG_TABLE_NAME = 'gf_launches' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'gf_launches rows cannot be deleted';
        END IF;
        IF OLD.ca IS NOT NULL AND NEW.ca IS DISTINCT FROM OLD.ca THEN
            RAISE EXCEPTION 'the CA of a launch is final';
        END IF;
        IF OLD.verdict <> 'pending' AND (NEW.verdict IS DISTINCT FROM OLD.verdict
                                         OR NEW.verdict_at IS DISTINCT FROM OLD.verdict_at) THEN
            RAISE EXCEPTION 'the verdict is locked';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS gf_ideas_guard ON gf_ideas;
CREATE TRIGGER gf_ideas_guard BEFORE UPDATE OR DELETE ON gf_ideas FOR EACH ROW EXECUTE FUNCTION gf_guard();
DROP TRIGGER IF EXISTS gf_rounds_guard ON gf_rounds;
CREATE TRIGGER gf_rounds_guard BEFORE UPDATE OR DELETE ON gf_rounds FOR EACH ROW EXECUTE FUNCTION gf_guard();
DROP TRIGGER IF EXISTS gf_launches_guard ON gf_launches;
CREATE TRIGGER gf_launches_guard BEFORE UPDATE OR DELETE ON gf_launches FOR EACH ROW EXECUTE FUNCTION gf_guard();
