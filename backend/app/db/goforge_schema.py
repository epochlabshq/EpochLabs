"""
Idempotent schema for GoForge. Applied by the GoForge worker at startup and mirrored in DDL.sql.

The public record cannot be edited quietly, so the rules are enforced by the database as well as the app:
launch rows can never be deleted, identity columns never change once set, and a locked verdict is final.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    """
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
        updated_at      TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goforge_snapshots (
        launch_id       TEXT REFERENCES goforge_launches(id),
        ts              TIMESTAMPTZ NOT NULL,
        price_usd       NUMERIC,
        mc_usd          NUMERIC,
        liquidity_usd   NUMERIC,
        volume_24h_usd  NUMERIC,
        holders         INTEGER,
        PRIMARY KEY (launch_id, ts)
    )
    """,
    # Set once DexScreener reports a pool with liquidity. Nothing about a launch is public before this.
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS pool_active_at TIMESTAMPTZ",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS pair_url TEXT",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS decimals INTEGER",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS total_supply NUMERIC",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS deployer TEXT",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS top10_pct DOUBLE PRECISION",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS top10_at TIMESTAMPTZ",
    # Last time a market read succeeded: drives the "stale" label
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS last_source_ok_at TIMESTAMPTZ",
    "ALTER TABLE goforge_launches ADD COLUMN IF NOT EXISTS epc_burned_at TIMESTAMPTZ",
    """
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
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS goforge_launches_immutable ON goforge_launches",
    """
    CREATE TRIGGER goforge_launches_immutable BEFORE UPDATE OR DELETE ON goforge_launches
    FOR EACH ROW EXECUTE FUNCTION goforge_launches_guard()
    """,
]


async def ensure_goforge_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
