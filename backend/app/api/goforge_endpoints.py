import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.goforge_config import GoForgeConfigError, LaunchEntry, load_launches
from app.db.database import get_db
from app.db.goforge_schema import ensure_goforge_schema
from app.api.desk_endpoints import _cached_inputs, build_trade_views
from app.services.desk import WEI
from app.services.desk import totals as desk_totals
from app.services.goforge import (
    evaluate_gate, is_public, net_pnl_after_gas, serialize_history, serialize_launch, totals,
)

router = APIRouter(prefix="/api")

CACHE_TTL_SECONDS = 15.0
_cache: Optional[dict] = None
_cache_ts = 0.0
_schema_ready = False


def invalidate_goforge_cache() -> None:
    global _cache_ts
    _cache_ts = 0.0


def _entries_by_id() -> dict[str, LaunchEntry]:
    try:
        return {e.id: e for e in load_launches()}
    except (GoForgeConfigError, ValueError, OSError) as e:
        print(f"[API ERROR] goforge config unreadable: {e}", flush=True)
        return {}


def _entry_for(row: dict, entries: dict[str, LaunchEntry]) -> LaunchEntry:
    """A launch row always has an entry. If the config entry was removed (it must not be), the record stays."""
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
    if not _schema_ready:  # the page works even where the worker is disabled or has not started yet
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


def build_gate(rows: list[dict], desk: Optional[dict], now: datetime) -> Optional[dict]:
    """The Launch Gate, or None when the Desk data it depends on cannot be read (never a guessed gate)."""
    if desk is None:
        return None
    public = [r for r in rows if is_public(r) and r.get("launched_at")]
    last = max((r["launched_at"] for r in public), default=None)
    net = net_pnl_after_gas(desk["realized_eth"], desk["closed"], settings.GOFORGE_GAS_ETH_PER_TX)
    return evaluate_gate(
        closed_trades=desk["closed"], min_trades=settings.GOFORGE_GATE_MIN_TRADES, net_pnl_eth=net,
        model_fix_done=settings.GOFORGE_MODEL_FIX_DONE, last_launch_at=last,
        cooldown_days=settings.GOFORGE_COOLDOWN_DAYS, balance_eth=desk["balance_eth"],
        reserve_eth=settings.GOFORGE_LAUNCH_RESERVE_ETH,
        pending_launch=any(r.get("verdict") == "pending" for r in public), now=now,
    )


def build_goforge_payload(rows: list[dict], latest: dict[str, dict], desk: Optional[dict], now: datetime) -> dict:
    launches = serialize_rows(rows, latest, now)
    return {
        "gate": build_gate(rows, desk, now),
        "totals": totals(launches),
        "launches": launches,
        "generated_at": now.isoformat(),
    }


async def load_desk_gate_inputs(db: AsyncSession) -> Optional[dict]:
    """Closed-trade count, realized PnL and wallet balance from the Desk's cached snapshot."""
    try:
        inp = await _cached_inputs(db)
    except HTTPException:
        return None
    open_rows, closed_rows = build_trade_views(inp)
    t = desk_totals(open_rows, closed_rows, settings.DESK_START_ETH)
    return {"closed": len(closed_rows), "realized_eth": t["realized_eth"],
            "balance_eth": None if inp.balance_wei is None else inp.balance_wei / WEI}


@router.get("/goforge")
async def get_goforge(db: AsyncSession = Depends(get_db)):
    """GET /api/goforge - Launch Gate, totals and every public launch (a launch appears once its pool is active)."""
    global _cache, _cache_ts
    if _cache is not None and time.time() - _cache_ts < CACHE_TTL_SECONDS:
        return _cache
    try:
        rows, latest = await load_rows(db)
        desk = await load_desk_gate_inputs(db)
    except Exception as e:
        print(f"[API ERROR] goforge failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="GoForge data unavailable")
    _cache = build_goforge_payload(rows, latest, desk, datetime.now(timezone.utc))
    _cache_ts = time.time()
    return _cache


@router.get("/goforge/{launch_id}/history")
async def get_goforge_history(launch_id: str, db: AsyncSession = Depends(get_db)):
    """GET /api/goforge/{id}/history - MC and price snapshots of the first 48 hours, for the card chart."""
    row = (await db.execute(text("SELECT * FROM goforge_launches WHERE id = :id"), {"id": launch_id})).mappings().first()
    # Not public yet counts as not found: the response must not confirm that an upcoming launch exists
    if row is None or not is_public(dict(row)):
        raise HTTPException(status_code=404, detail="Launch not found")
    snaps = (await db.execute(text(
        "SELECT ts, price_usd, mc_usd FROM goforge_snapshots WHERE launch_id = :id ORDER BY ts"
    ), {"id": launch_id})).mappings().all()
    return serialize_history([dict(s) for s in snaps], row["launched_at"], hours=settings.GOFORGE_VERDICT_HOURS,
                             target_usd=settings.GOFORGE_TARGET_MC_USD)
