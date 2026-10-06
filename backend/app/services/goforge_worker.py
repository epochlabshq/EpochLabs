"""
GoForge watcher. Every GOFORGE_WORKER_INTERVAL_SECONDS (60s):
1. re-read config/goforge.launches.json (a new entry is picked up with no restart),
2. for each launch pull Blockscout + DexScreener (RPC as fallback), store a snapshot, track the peak MC,
3. at hour 48 lock the verdict (reached_30k / stalled): once locked it is never rewritten,
4. broadcast goforge_update / goforge_verdict / goforge_burn.

A launch stays invisible (no row in the API, nothing on the stream) until its pool is active.
A launch with a locked verdict is refreshed every GOFORGE_SETTLED_INTERVAL_SECONDS only, and with no config
entries the cycle does no database work at all (the database operation budget is tight).
Every WS payload comes from the same serializer as GET /api/goforge.
"""
import asyncio
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.api.goforge_endpoints import invalidate_goforge_cache, load_rows, serialize_rows
from app.api.websocket import manager
from app.core.config import settings
from app.core.goforge_config import GoForgeConfigError, LaunchEntry, all_entries
from app.db.database import AsyncSessionLocal
from app.db.goforge_schema import ensure_goforge_schema
from app.db.locks import exclusive
from app.services.goforge import (
    decide_verdict, holders_concentration, next_peak,
)
from app.services.goforge_sources import DexResult, GoForgeSources

GOFORGE_WORKER_LOCK_KEY = 0x45504F43_464F5247  # "EPOC" "FORG"

UPDATABLE = frozenset({
    "name", "symbol", "launched_at", "deployer", "decimals", "total_supply", "pool_active_at", "pair_url",
    "peak_mc_usd", "verdict", "verdict_at", "fees_usd", "epc_burned", "epc_burned_at", "top10_pct", "top10_at",
    "last_source_ok_at", "updated_at",
})

_last_refresh: dict[str, float] = {}
_settled: set[str] = set()
_last_top_holders: dict[str, float] = {}
_last_holders_fallback: dict[str, float] = {}


@dataclass
class Observation:
    """What the sources returned for one launch this cycle. None means that source could not be read."""
    dex: DexResult = field(default_factory=lambda: DexResult(False))
    token: Optional[dict] = None
    launch: Optional[dict] = None
    holders: Optional[int] = None
    top_holders: Optional[list[dict]] = None
    epc_burned: Optional[float] = None
    fees_eth: Optional[float] = None
    eth_usd: Optional[float] = None


@dataclass
class RefreshPlan:
    updates: dict = field(default_factory=dict)
    snapshot: Optional[dict] = None
    verdict_locked: Optional[str] = None
    became_public: bool = False
    burn_delta: Optional[float] = None


def plan_refresh(row: dict, entry: LaunchEntry, obs: Observation, now: datetime, *, hours: int,
                 target_usd: float) -> RefreshPlan:
    """Decide what to write for one launch. Pure: no I/O, so every rule below is unit-tested."""
    plan = RefreshPlan()
    u = plan.updates
    pair = obs.dex.pair

    if row.get("launched_at") is None and obs.launch and obs.launch.get("launched_at"):
        u["launched_at"] = obs.launch["launched_at"]
        if obs.launch.get("deployer"):
            u["deployer"] = obs.launch["deployer"]
    launched_at = row.get("launched_at") or u.get("launched_at")

    token = obs.token or {}
    for col, val in (("name", token.get("name") or (pair.name if pair else None)),
                     ("symbol", token.get("symbol") or (pair.symbol if pair else None)),
                     ("decimals", token.get("decimals")), ("total_supply", token.get("total_supply"))):
        if row.get(col) is None and val is not None:
            u[col] = val

    # Public only with a live pool that has liquidity AND a known launch time (the 48h clock needs it)
    active_at = row.get("pool_active_at")
    if active_at is None and pair is not None and pair.liquidity_usd > 0 and launched_at is not None:
        u["pool_active_at"] = now
        plan.became_public = True
        active_at = now
    public = active_at is not None

    verdict = row.get("verdict") or "pending"
    peak = float(row.get("peak_mc_usd") or 0)
    if public and pair is not None:
        if pair.pair_url and pair.pair_url != row.get("pair_url"):
            u["pair_url"] = pair.pair_url
        ts = now.replace(microsecond=0)
        plan.snapshot = {"ts": ts, "price_usd": pair.price_usd, "mc_usd": pair.mc_usd,
                         "liquidity_usd": pair.liquidity_usd, "volume_24h_usd": pair.volume_24h_usd,
                         "holders": obs.holders}
        # A failed read writes nothing, so last_source_ok_at ages and the card says "stale"
        u["last_source_ok_at"] = now
        new_peak = next_peak(peak, pair.mc_usd, ts, launched_at, hours, verdict)
        if new_peak != peak:
            u["peak_mc_usd"] = peak = new_peak
    if public:
        # Decided from the stored peak even if DexScreener is down at hour 48
        locked = decide_verdict(verdict, launched_at, now, peak, hours=hours, target_usd=target_usd)
        if locked:
            u["verdict"], u["verdict_at"] = locked, now
            plan.verdict_locked = locked

    supply = u.get("total_supply", row.get("total_supply"))
    if obs.top_holders is not None:
        pct = holders_concentration(obs.top_holders, float(supply) if supply else None)
        if pct is not None:
            u["top10_pct"], u["top10_at"] = pct, now

    if entry.fee_router_address:
        if obs.epc_burned is not None:
            u["epc_burned"], u["epc_burned_at"] = obs.epc_burned, now
            delta = obs.epc_burned - float(row.get("epc_burned") or 0)
            if delta > 0:
                plan.burn_delta = delta
        if obs.fees_eth is not None and obs.eth_usd:
            u["fees_usd"] = obs.fees_eth * obs.eth_usd

    if u:
        u["updated_at"] = now
    return plan


async def observe(sources, entry: LaunchEntry, row: dict, now_ts: float) -> Observation:
    """Network side of one launch. Metadata is fetched until known; the cheap reads run every cycle."""
    obs = Observation()
    calls = {"dex": sources.dex(entry.ca), "token": sources.token(entry.ca)}
    if row.get("launched_at") is None:
        calls["launch"] = sources.launch_tx(entry.launch_tx)
    if now_ts - _last_top_holders.get(entry.id, 0) > settings.GOFORGE_HOLDERS_LIST_INTERVAL_SECONDS:
        calls["top_holders"] = sources.top_holders(entry.ca)
    if entry.fee_router_address:
        calls["epc_burned"] = sources.epc_burned(entry.fee_router_address)
        calls["fees_eth"] = sources.fees_eth(entry.fee_router_address)
        calls["eth_usd"] = sources.eth_usd()
    results = dict(zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True)))
    for k, v in results.items():
        if isinstance(v, Exception):
            print(f"[GOFORGE] {entry.id} {k} failed: {type(v).__name__} {v}", flush=True)
            results[k] = DexResult(False) if k == "dex" else None
    obs.dex = results["dex"]
    obs.token = results["token"]
    obs.launch = results.get("launch")
    obs.top_holders = results.get("top_holders")
    if "top_holders" in results and obs.top_holders is not None:
        _last_top_holders[entry.id] = now_ts
    obs.epc_burned = results.get("epc_burned")
    obs.fees_eth = results.get("fees_eth")
    obs.eth_usd = results.get("eth_usd")

    if obs.token is None and (row.get("name") is None or row.get("decimals") is None):
        obs.token = await sources.token_via_rpc(entry.ca)
    obs.holders = (obs.token or {}).get("holders")
    launched_at = row.get("launched_at") or (obs.launch or {}).get("launched_at")
    if obs.holders is None and launched_at and obs.dex.pair is not None \
            and now_ts - _last_holders_fallback.get(entry.id, 0) > settings.GOFORGE_HOLDERS_LIST_INTERVAL_SECONDS:
        _last_holders_fallback[entry.id] = now_ts
        obs.holders = await sources.holders_via_rpc(entry.ca, launched_at)
    return obs


async def apply_plan(db, launch_id: str, plan: RefreshPlan) -> None:
    if plan.updates:
        cols = set(plan.updates)
        assert cols <= UPDATABLE, f"unexpected columns {cols - UPDATABLE}"
        sets = ", ".join(f"{c} = :{c}" for c in plan.updates)
        await db.execute(text(f"UPDATE goforge_launches SET {sets} WHERE id = :_id"),
                         {**plan.updates, "_id": launch_id})
    if plan.snapshot:
        await db.execute(text(
            "INSERT INTO goforge_snapshots (launch_id, ts, price_usd, mc_usd, liquidity_usd, volume_24h_usd, holders) "
            "VALUES (:id, :ts, :price_usd, :mc_usd, :liquidity_usd, :volume_24h_usd, :holders) "
            "ON CONFLICT (launch_id, ts) DO NOTHING"
        ), {"id": launch_id, **plan.snapshot})


def due_entries(entries: list[LaunchEntry], now_ts: float) -> list[LaunchEntry]:
    out = []
    for e in entries:
        interval = (settings.GOFORGE_SETTLED_INTERVAL_SECONDS if e.id in _settled
                    else settings.GOFORGE_WORKER_INTERVAL_SECONDS)
        if now_ts - _last_refresh.get(e.id, 0) >= interval - 1:
            out.append(e)
    return out


async def run_goforge_cycle(sources=None) -> None:
    try:
        entries = all_entries()
    except (GoForgeConfigError, ValueError, OSError) as e:
        print(f"[GOFORGE WORKER] Config unreadable, skipping cycle: {e}", flush=True)
        return
    now_ts = time.time()
    due = due_entries(entries, now_ts)
    if not due:
        return  # nothing configured or nothing due: no database work
    sources = sources or GoForgeSources()
    events: list[dict] = []
    async with exclusive(GOFORGE_WORKER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            return
        try:
            for e in due:
                await db.execute(text(
                    "INSERT INTO goforge_launches (id, ca, launch_tx) VALUES (:id, :ca, :tx) ON CONFLICT (id) DO NOTHING"
                ), {"id": e.id, "ca": e.ca, "tx": e.launch_tx})
            await db.commit()
            rows, latest = await load_rows(db)
            by_id = {r["id"]: r for r in rows}
            known = {e.id for e in entries}
            for r in rows:
                if r["id"] not in known:
                    print(f"[GOFORGE WORKER] WARNING: {r['id']} is stored but missing from the config. "
                          "Entries must never be removed.", flush=True)
            plans: dict[str, RefreshPlan] = {}
            now = datetime.now(timezone.utc)
            for e in due:
                row = by_id[e.id]
                obs = await observe(sources, e, row, now_ts)
                plan = plan_refresh(row, e, obs, now, hours=settings.GOFORGE_VERDICT_HOURS,
                                    target_usd=settings.GOFORGE_TARGET_MC_USD)
                await apply_plan(db, e.id, plan)
                plans[e.id] = plan
                _last_refresh[e.id] = now_ts
                verdict = plan.updates.get("verdict") or row.get("verdict")
                if verdict and verdict != "pending":
                    _settled.add(e.id)
            await db.commit()

            if any(p.updates or p.snapshot for p in plans.values()):
                rows, latest = await load_rows(db)
                cards = {c["id"]: c for c in serialize_rows(rows, latest, now)}
                for lid, plan in plans.items():
                    card = cards.get(lid)
                    if card is None:
                        continue  # not public yet: nothing about it may leave the backend
                    if plan.snapshot or plan.updates:
                        events.append({"goforge_update": card})
                    if plan.verdict_locked:
                        events.append({"goforge_verdict": {"id": lid, "verdict": plan.verdict_locked,
                                                           "verdict_at": card["verdict_at"], "launch": card}})
                    if plan.burn_delta:
                        events.append({"goforge_burn": {"id": lid, "epc_burned": card["epc_burned"],
                                                        "delta": plan.burn_delta}})
                    if plan.became_public:
                        print(f"[GOFORGE WORKER] {lid} is public (pool active).", flush=True)
                    if plan.verdict_locked:
                        print(f"[GOFORGE WORKER] {lid} verdict locked: {plan.verdict_locked}", flush=True)
        finally:
            await db.rollback()

    if events:
        invalidate_goforge_cache()
    for ev in events:
        await manager.broadcast(ev)


async def start_goforge_worker_loop():
    if not settings.GOFORGE_WORKER_ENABLED:
        print("[GOFORGE WORKER] Disabled via GOFORGE_WORKER_ENABLED.")
        return
    schema_ready = False
    print(f"[GOFORGE WORKER] Started (interval {settings.GOFORGE_WORKER_INTERVAL_SECONDS}s).")
    while True:
        try:
            if not schema_ready:
                async with AsyncSessionLocal() as db:
                    await ensure_goforge_schema(db)
                schema_ready = True
            await run_goforge_cycle()
        except Exception as e:
            print(f"[GOFORGE WORKER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(settings.GOFORGE_WORKER_INTERVAL_SECONDS)
