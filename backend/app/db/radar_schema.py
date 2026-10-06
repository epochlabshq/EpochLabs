"""
Idempotent schema for Meta Radar. Applied by the Radar worker and the API on first use, mirrored in DDL.sql.

Points keep the token address (needed to join examples back to `tokens`) but the API never selects it.
`centroid_vec` is the 384-d cluster centroid: tomorrow's run matches against it to keep cluster ids stable.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS radar_runs (
        run_id          TEXT PRIMARY KEY,
        run_at          TIMESTAMPTZ NOT NULL,
        n_tokens        INTEGER,
        n_resolved      INTEGER,
        baseline_rate   NUMERIC,
        method          TEXT,
        params_json     JSONB
    )
    """,
    """
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
    )
    """,
    """
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
    )
    """,
    "ALTER TABLE radar_clusters ADD COLUMN IF NOT EXISTS centroid_vec DOUBLE PRECISION[]",
    "CREATE INDEX IF NOT EXISTS idx_radar_points_cluster ON radar_points (run_id, cluster_id)",
]


async def ensure_radar_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
