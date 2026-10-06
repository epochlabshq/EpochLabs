"""
Test helper: a real PostgreSQL (pgserver, an embedded build) so the SQL, the triggers and the lifecycle are tested for
real instead of mocked. Tests that need it call `require_pg()` and are skipped when pgserver is not installed.
Never touches DATABASE_URL: the server lives in a temp directory.
"""
import asyncio
import atexit
import shutil
import tempfile
import unittest
from contextlib import asynccontextmanager

try:
    import pgserver
except ImportError:  # pragma: no cover
    pgserver = None

_server = None
_dir = None
_ready = False

BASE_SCHEMA = [
    # what the registry reads from the existing app
    "CREATE TABLE IF NOT EXISTS tokens (mint TEXT PRIMARY KEY, symbol TEXT, name TEXT, chain TEXT)",
]


def require_pg():
    if pgserver is None:
        raise unittest.SkipTest("pgserver is not installed")


def _start():
    global _server, _dir
    if _server is None:
        _dir = tempfile.mkdtemp(prefix="gf_pg_")
        _server = pgserver.get_server(_dir, cleanup_mode="stop")
        atexit.register(_stop)
    return _server


def _stop():
    global _server
    try:
        if _server is not None:
            _server.cleanup()
    finally:
        _server = None
        if _dir:
            shutil.rmtree(_dir, ignore_errors=True)


def async_url() -> str:
    uri = _start().get_uri()
    return uri.replace("postgresql://", "postgresql+asyncpg://", 1)


def engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    return create_async_engine(async_url(), poolclass=NullPool)


@asynccontextmanager
async def session():
    """A session on a fresh connection (NullPool), so it is bound to the running event loop."""
    from sqlalchemy.ext.asyncio import AsyncSession
    eng = engine()
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            yield db
    finally:
        await eng.dispose()


async def _apply():
    from sqlalchemy import text
    from app.db.gf_schema import ensure_gf_schema
    from app.db.goforge_schema import ensure_goforge_schema
    async with session() as db:
        for stmt in BASE_SCHEMA:
            await db.execute(text(stmt))
        await db.commit()
        await ensure_goforge_schema(db)
        await ensure_gf_schema(db)


def setup_schema():
    global _ready
    require_pg()
    if not _ready:
        asyncio.run(_apply())
        _ready = True


async def truncate_all():
    from sqlalchemy import text
    async with session() as db:
        await db.execute(text(
            "TRUNCATE gf_distributions, gf_votes, gf_vote_events, gf_launches, gf_ideas, gf_rounds, gf_creators, gf_nonces, "
            "gf_oauth_states, gf_wallet_changes, gf_posts, goforge_snapshots, goforge_launches, tokens CASCADE"))
        await db.commit()
