"""
Idempotent schema for the GoForge Registry (community launch). Applied by the round worker and the API on first use,
mirrored in DDL.sql.

The tables of the brief (gf_creators, gf_rounds, gf_ideas, gf_votes, gf_launches) come first; the extra tables hold what
the brief needs but does not name: one-time SIWE nonces, X OAuth PKCE state, the wallet-change audit log, the vote log
used for rate limiting, and the fee distributions shown with their tx hash.

The database enforces the rules that must not depend on application code: a winner never changes once set, ideas are
never deleted, a locked verdict is final, a fee tx can only pay for one idea.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gf_rounds (
        round_date        DATE PRIMARY KEY,
        status            TEXT CHECK (status IN ('submit','review','vote','scoring','announced','launched','no_launch')),
        winner_idea_id    TEXT,
        n_ideas           INTEGER,
        n_votes           INTEGER,
        announced_at      TIMESTAMPTZ,
        no_launch_reason  TEXT
    )
    """,
    "ALTER TABLE gf_rounds ADD COLUMN IF NOT EXISTS votes_closed_at TIMESTAMPTZ",
    "ALTER TABLE gf_rounds ADD COLUMN IF NOT EXISTS scoreboard_sha256 TEXT",
    """
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
    )
    """,
    "ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS auto_flags JSONB",
    "ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
    "ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS reviewed_by TEXT",
    "ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS votes_counted INTEGER",
    "ALTER TABLE gf_ideas ADD COLUMN IF NOT EXISTS lore_embedding DOUBLE PRECISION[]",
    "CREATE INDEX IF NOT EXISTS idx_gf_ideas_round ON gf_ideas (round_date, status)",
    "CREATE INDEX IF NOT EXISTS idx_gf_ideas_wallet ON gf_ideas (wallet, round_date)",
    """
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
    )
    """,
    # Every vote and every change of vote: the hourly change limit counts these
    "ALTER TABLE gf_votes ADD COLUMN IF NOT EXISTS signed_at BIGINT",
    """
    CREATE TABLE IF NOT EXISTS gf_vote_events (
        id            BIGSERIAL PRIMARY KEY,
        voter_wallet  TEXT NOT NULL,
        round_date    DATE NOT NULL,
        idea_id       TEXT NOT NULL,
        at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gf_vote_events_wallet ON gf_vote_events (voter_wallet, at)",
    """
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
    )
    """,
    # Golem picks the launch hour at announcement; the CA only exists once the token is live
    "ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS launch_due_at TIMESTAMPTZ",
    "ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS launch_hour_reason TEXT",
    "ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS last_distribute_at TIMESTAMPTZ",
    "ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS dist_block BIGINT",
    "ALTER TABLE gf_launches ADD COLUMN IF NOT EXISTS verdict_synced BOOLEAN DEFAULT FALSE",
    """
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
    )
    """,
    # X posts, each sent once (winner / live / verdict per idea, no_launch per round)
    """
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
    )
    """,
    # SIWE: a nonce is valid once
    """
    CREATE TABLE IF NOT EXISTS gf_nonces (
        nonce       TEXT PRIMARY KEY,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        used_at     TIMESTAMPTZ
    )
    """,
    # X OAuth 2.0 PKCE: state -> the wallet that started it and the code verifier
    """
    CREATE TABLE IF NOT EXISTS gf_oauth_states (
        state          TEXT PRIMARY KEY,
        wallet         TEXT NOT NULL,
        code_verifier  TEXT NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # A wallet <-> X account pairing changes only through a manual, logged process
    """
    CREATE TABLE IF NOT EXISTS gf_wallet_changes (
        id           BIGSERIAL PRIMARY KEY,
        x_user_id    TEXT NOT NULL,
        old_wallet   TEXT,
        new_wallet   TEXT,
        reason       TEXT NOT NULL,
        changed_by   TEXT NOT NULL,
        at           TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
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
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS gf_ideas_guard ON gf_ideas",
    "CREATE TRIGGER gf_ideas_guard BEFORE UPDATE OR DELETE ON gf_ideas FOR EACH ROW EXECUTE FUNCTION gf_guard()",
    "DROP TRIGGER IF EXISTS gf_rounds_guard ON gf_rounds",
    "CREATE TRIGGER gf_rounds_guard BEFORE UPDATE OR DELETE ON gf_rounds FOR EACH ROW EXECUTE FUNCTION gf_guard()",
    "DROP TRIGGER IF EXISTS gf_launches_guard ON gf_launches",
    "CREATE TRIGGER gf_launches_guard BEFORE UPDATE OR DELETE ON gf_launches FOR EACH ROW EXECUTE FUNCTION gf_guard()",
]


async def ensure_gf_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
