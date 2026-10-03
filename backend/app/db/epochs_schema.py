"""
Idempotent schema for the Epochs page. Executed statement by statement at Epoch Watcher startup
(asyncpg cannot run multi-statement strings) and mirrored in DDL.sql for reference.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    # One row per epoch. Only `locked` or `complete` is stored: `active` is derived at read time.
    """
    CREATE TABLE IF NOT EXISTS epochs (
        id            SMALLINT PRIMARY KEY CHECK (id BETWEEN 1 AND 6),
        key           TEXT NOT NULL UNIQUE,
        status        TEXT NOT NULL DEFAULT 'locked' CHECK (status IN ('locked', 'complete')),
        proof_json    JSONB,
        completed_at  TIMESTAMPTZ,
        CHECK (status <> 'complete' OR (proof_json IS NOT NULL AND completed_at IS NOT NULL))
    )
    """,
    """
    INSERT INTO epochs (id, key) VALUES
        (1, 'ingestion'), (2, 'hourglass_fill'), (3, 'first_trade'),
        (4, 'first_burn'), (5, 'golem_launch'), (6, 'open_golem')
    ON CONFLICT (id) DO NOTHING
    """,
    # Completion is permanent and strictly ordered, enforced in the database as well as in code.
    """
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
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_epochs_guard ON epochs",
    """
    CREATE TRIGGER trg_epochs_guard
    BEFORE UPDATE OR DELETE ON epochs
    FOR EACH ROW EXECUTE FUNCTION epochs_guard()
    """,
    # Per-indexer scan cursor (last fully indexed block)
    """
    CREATE TABLE IF NOT EXISTS chain_cursor (
        name        TEXT PRIMARY KEY,
        last_block  BIGINT NOT NULL
    )
    """,
    # Successful swaps sent by the Golem wallet to the Uniswap V2 router (Epoch III, later the Trade Journal)
    """
    CREATE TABLE IF NOT EXISTS golem_swaps (
        tx_hash  TEXT PRIMARY KEY,
        block    BIGINT NOT NULL,
        at       TIMESTAMPTZ NOT NULL
    )
    """,
    # Swap details decoded from the receipt (Trade Journal)
    "ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS side TEXT",
    "ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS token TEXT",
    "ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS token_amount_wei NUMERIC(78, 0)",
    "ALTER TABLE golem_swaps ADD COLUMN IF NOT EXISTS eth_amount_wei NUMERIC(78, 0)",
    # What Golem decided before sending a trade: written by the executor through app.services.golem_guard,
    # never by hand. Joined to golem_swaps on tx_hash once the tx is sent.
    """
    CREATE TABLE IF NOT EXISTS golem_trade_decisions (
        id                    BIGSERIAL PRIMARY KEY,
        decided_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        token                 TEXT NOT NULL,
        side                  TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
        survival_probability  DOUBLE PRECISION NOT NULL CHECK (survival_probability BETWEEN 0 AND 1),
        top_signal            TEXT NOT NULL,
        run_id                BIGINT NOT NULL REFERENCES model_runs(id),
        tx_hash               TEXT UNIQUE
    )
    """,
    # $EPC transfers from the Golem wallet to the burn address (Epoch IV + burn totals)
    """
    CREATE TABLE IF NOT EXISTS epc_burns (
        tx_hash       TEXT NOT NULL,
        log_index     INTEGER NOT NULL,
        block         BIGINT NOT NULL,
        at            TIMESTAMPTZ NOT NULL,
        burn_address  TEXT NOT NULL,
        amount_wei    NUMERIC(78, 0) NOT NULL,
        PRIMARY KEY (tx_hash, log_index)
    )
    """,
    # EpochLauncher Launched + AgentUpdated logs (Epoch V)
    """
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
    )
    """,
]


async def ensure_epochs_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
