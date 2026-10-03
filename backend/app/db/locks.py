"""
Cross-instance locks for the background workers.

Session-level advisory locks (pg_try_advisory_lock / pg_advisory_unlock) leak with SQLAlchemy sessions: a
session hands its connection back to the pool at every commit, so the unlock often runs on a different
connection and the lock stays held for good. Behind a transaction pooler the same happens across server
connections. A transaction-level lock taken on one dedicated connection, kept open for the whole critical
section, is released by Postgres when that transaction ends, however it ends.
"""
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.db.database import engine

# Distinct from the old session-lock keys, which may still be held by leaked pooled connections
LOCK_NAMESPACE = 0x584C  # "XL"
SCHEMA_LOCK_KEY = 0x45504F43_53434845  # "EPOC" "SCHE"


def xact_key(key: int) -> int:
    return (key ^ (LOCK_NAMESPACE << 48)) & 0x7FFF_FFFF_FFFF_FFFF


@asynccontextmanager
async def exclusive(key: int):
    """`async with exclusive(KEY) as got:` runs the block with got=True in at most one process at a time."""
    async with engine.connect() as conn:
        async with conn.begin():
            got = (await conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": xact_key(key)})).scalar()
            yield bool(got)


async def apply_schema(db, statements: list[str]) -> None:
    """Run idempotent DDL in one transaction, serialized across workers (concurrent DDL can deadlock)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": xact_key(SCHEMA_LOCK_KEY)})
    for stmt in statements:
        await db.execute(text(stmt))
    await db.commit()
