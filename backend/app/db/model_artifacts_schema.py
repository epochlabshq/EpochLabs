"""
Idempotent schema for stored model artifacts (app.ml.artifact). Applied by the model worker before it saves
a run, and mirrored in DDL.sql for reference.
"""
from app.db.locks import apply_schema

SCHEMA_STATEMENTS: list[str] = [
    # One artifact per model run, written in the same transaction as the run. Canonical JSON + its sha256.
    """
    CREATE TABLE IF NOT EXISTS model_artifacts (
        run_id      BIGINT PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        sha256      TEXT NOT NULL,
        artifact    TEXT NOT NULL
    )
    """,
    # The model behind a published run never changes after the fact.
    """
    CREATE OR REPLACE FUNCTION model_artifacts_guard() RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'model_artifacts rows are immutable';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS model_artifacts_immutable ON model_artifacts",
    """
    CREATE TRIGGER model_artifacts_immutable BEFORE UPDATE ON model_artifacts
    FOR EACH ROW EXECUTE FUNCTION model_artifacts_guard()
    """,
]


async def ensure_model_artifacts_schema(db) -> None:
    await apply_schema(db, SCHEMA_STATEMENTS)
