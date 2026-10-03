"""
Idempotent schema for The Desk. Applied by the Desk worker at startup, after the Epochs schema (it extends
golem_trade_decisions), and mirrored in DDL.sql for reference.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    # Live survival score per watched token, from the latest model run's artifact (app.services.scorer)
    """
    CREATE TABLE IF NOT EXISTS desk_scores (
        mint         TEXT PRIMARY KEY,
        run_id       BIGINT NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
        survival     DOUBLE PRECISION NOT NULL CHECK (survival BETWEEN 0 AND 1),
        top_signals  JSONB NOT NULL,
        scored_at    TIMESTAMPTZ NOT NULL
    )
    """,
    # Current holder count per watched token, counted onchain (app.services.live_holders). Kept apart from
    # tokens.holders, which is the 48h label sample the model trains on.
    """
    CREATE TABLE IF NOT EXISTS desk_live_holders (
        mint        TEXT PRIMARY KEY,
        holders     INTEGER NOT NULL CHECK (holders >= 0),
        block       BIGINT NOT NULL,
        sampled_at  TIMESTAMPTZ NOT NULL
    )
    """,
    # False when the address has no contract on Robinhood Chain (feed rows from other chains): never scored or shown
    "ALTER TABLE desk_live_holders ADD COLUMN IF NOT EXISTS has_code BOOLEAN NOT NULL DEFAULT true",
    # Live market cap per watched token from DexScreener (app.services.live_market); peak_seen_usd is the
    # highest market cap the Desk itself has observed
    """
    CREATE TABLE IF NOT EXISTS desk_market (
        mint           TEXT PRIMARY KEY,
        mc_usd         DOUBLE PRECISION,
        liq_usd        DOUBLE PRECISION,
        price_usd      DOUBLE PRECISION,
        pair_url       TEXT,
        dex_id         TEXT,
        peak_seen_usd  DOUBLE PRECISION,
        fetched_at     TIMESTAMPTZ NOT NULL
    )
    """,
    # Candidate lifecycle, written by the executor. The token column never leaves the backend before the
    # entry tx confirms (app.services.desk.anonymize_waiting is the only reader that serializes it).
    """
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
    )
    """,
    # Golem's state and heartbeat (single row)
    """
    CREATE TABLE IF NOT EXISTS desk_state (
        id                INT PRIMARY KEY CHECK (id = 1),
        state             TEXT NOT NULL,
        changed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_decision_at  TIMESTAMPTZ
    )
    """,
    "INSERT INTO desk_state (id, state) VALUES (1, 'gated') ON CONFLICT (id) DO NOTHING",
    # Each trade is announced once per kind (WS now, X auto-post later), surviving restarts
    """
    CREATE TABLE IF NOT EXISTS desk_announcements (
        trade_id      TEXT NOT NULL,
        kind          TEXT NOT NULL CHECK (kind IN ('open', 'close')),
        tx_hash       TEXT NOT NULL,
        announced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (trade_id, kind)
    )
    """,
    # X auto-post queue state on each announcement (app.services.desk_poster)
    "ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS event_at TIMESTAMPTZ",
    """
    ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS post_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (post_status IN ('pending', 'posted', 'dry_run', 'failed', 'skipped'))
    """,
    "ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS post_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS tweet_id TEXT",
    "ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS post_error TEXT",
    "ALTER TABLE desk_announcements ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ",
    # The full "Why Golem entered" card, written once at decision time as canonical JSON with its sha256
    "ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_canonical TEXT",
    "ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_sha256 TEXT",
    """
    ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS exit_reason TEXT
        CHECK (exit_reason IN ('take_profit', 'stop_loss', 'max_hold'))
    """,
    # Decisions are append-only: only a missing tx_hash may be filled in afterwards
    """
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
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS golem_trade_decisions_append_only ON golem_trade_decisions",
    """
    CREATE TRIGGER golem_trade_decisions_append_only BEFORE UPDATE OR DELETE ON golem_trade_decisions
    FOR EACH ROW EXECUTE FUNCTION golem_trade_decisions_guard()
    """,
]


async def ensure_desk_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
