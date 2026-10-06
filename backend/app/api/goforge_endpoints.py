"""
GoForge launch tracking API: the live card of every launched token (market data, 48h verdict) and its price history.
The community registry (rounds, ideas, votes) lives in gf_endpoints; this module is what that archive builds on.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.goforge_config import GoForgeConfigError, LaunchEntry, all_entries
from app.db.database import get_db
from app.db.goforge_schema import ensure_goforge_schema
from app.services.goforge import is_public, serialize_history, serialize_launch, totals

router = APIRouter(prefix="/api/goforge")

_cache_ts = 0.0
_schema_ready = False


def invalidate_goforge_cache() -> None:
    """Kept for the watcher: it calls this after a cycle that changed something (the archive has its own short cache)."""
    global _cache_ts
    _cache_ts = 0.0
    try:
        from app.api import gf_endpoints
        gf_endpoints.invalidate_cache()
    except Exception:
        pass


def _entries_by_id() -> dict[str, LaunchEntry]:
    try:
        return {e.id: e for e in all_entries()}
    except (GoForgeConfigError, ValueError, OSError) as e:
        print(f"[API ERROR] goforge config unreadable: {e}", flush=True)
        return {}


def _entry_for(row: dict, entries: dict[str, LaunchEntry]) -> LaunchEntry:
    """A launch row always has an entry. If the entry is gone (it must not be), the record stays."""
    e = entries.get(row["id"])
    if e is not None:
        return e
    why = {k: None for k in ("window", "window_reason", "lore_summary", "model_run_id", "why_hash")}
    rules = {k: None for k in ("hook_address", "lp_lock_address", "lp_lock_until", "anti_snipe_blocks",
                               "wallet_cap_pct", "wallet_cap_minutes", "min_liquidity_usd")}
    return LaunchEntry(id=row["id"], ca=row["ca"], launch_tx=row["launch_tx"], why=why, rules=rules,
                       fee_router_address=None)


async def load_rows(db: AsyncSession) -> tuple[list[dict], dict[str, dict]]:
    """(launch rows, newest snapshot per launch). One query each, whatever the number of launches."""
    global _schema_ready
    if not _schema_ready:  # works even where the worker is disabled or has not started yet
        await ensure_goforge_schema(db)
        _schema_ready = True
    rows = [dict(r) for r in (await db.execute(text("SELECT * FROM goforge_launches"))).mappings().all()]
    latest = {r["launch_id"]: dict(r) for r in (await db.execute(text(
        "SELECT DISTINCT ON (launch_id) launch_id, ts, price_usd, mc_usd, liquidity_usd, volume_24h_usd, holders "
        "FROM goforge_snapshots ORDER BY launch_id, ts DESC"
    ))).mappings().all()}
    return rows, latest


def serialize_rows(rows: list[dict], latest: dict[str, dict], now: datetime) -> list[dict]:
    """Public launches only, newest first. The one serializer for REST and WS, so nothing leaks through either."""
    entries = _entries_by_id()
    out = []
    for row in sorted(rows, key=lambda r: r.get("launched_at") or r.get("pool_active_at") or now, reverse=True):
        card = serialize_launch(
            row, latest.get(row["id"]), _entry_for(row, entries), now,
            blockscout=settings.BLOCKSCOUT_BASE.rstrip("/"), dex_chain=settings.GOFORGE_DEX_CHAIN,
            hours=settings.GOFORGE_VERDICT_HOURS, stale_after_s=settings.GOFORGE_STALE_AFTER_SECONDS,
            settled_stale_after_s=settings.GOFORGE_SETTLED_INTERVAL_SECONDS * 2,
            public_url=settings.GOFORGE_PUBLIC_URL,
        )
        if card is not None:
            out.append(card)
    return out


def build_launches_payload(rows: list[dict], latest: dict[str, dict], now: datetime) -> dict:
    launches = serialize_rows(rows, latest, now)
    return {"totals": totals(launches), "launches": launches, "generated_at": now.isoformat()}


@router.get("/{launch_id}/history")
async def get_goforge_history(launch_id: str, db: AsyncSession = Depends(get_db)):
    """MC and price snapshots of the first 48 hours, for the card chart."""
    row = (await db.execute(text("SELECT * FROM goforge_launches WHERE id = :id"), {"id": launch_id})).mappings().first()
    # Not public yet counts as not found: the response must not confirm that an upcoming launch exists
    if row is None or not is_public(dict(row)):
        raise HTTPException(status_code=404, detail="Launch not found")
    snaps = (await db.execute(text(
        "SELECT ts, price_usd, mc_usd FROM goforge_snapshots WHERE launch_id = :id ORDER BY ts"
    ), {"id": launch_id})).mappings().all()
    return serialize_history([dict(s) for s in snaps], row["launched_at"], hours=settings.GOFORGE_VERDICT_HOURS,
                             target_usd=settings.GOFORGE_TARGET_MC_USD)
