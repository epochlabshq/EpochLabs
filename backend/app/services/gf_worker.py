"""
GoForge Registry round worker. One cycle every GF_WORKER_INTERVAL_SECONDS, but a cycle only touches the database when
something is due (the database operation budget is tight): a phase boundary was crossed, or the periodic sweep is due.

    12:00 UTC  submit -> vote            (status change, WS gf_phase)
    20:00 UTC  vote closed               (balances re-checked, scores, winner, scoreboard hash; WS gf_phase)
    20:05 UTC  announced                 (launch slot picked, X post, WS gf_winner)
    sweep      verdicts copied from the watcher, fee distributions indexed, keeper called, posts retried / queued

A round the worker slept through (restart, outage) is scored and announced as soon as the worker is back.
Every WS payload is built from the same data as the REST endpoints, so nothing leaks through the stream.
"""
import asyncio
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from app.api.websocket import manager
from app.core.config import settings
from app.core import goforge_config
from app.db.database import AsyncSessionLocal
from app.db.gf_schema import ensure_gf_schema
from app.db.goforge_schema import ensure_goforge_schema
from app.db.locks import exclusive
from app.services import gf_poster, gf_rounds, gf_service as svc, gf_store

GF_WORKER_LOCK_KEY = 0x45504F43_47464B52  # "EPOC" "GFKR"
SWEEP_SECONDS = 3600.0

_state = {"key": None, "sweep": 0.0, "bootstrapped": False}


def reset_state() -> None:
    _state.update(key=None, sweep=0.0, bootstrapped=False)


def due(now_ts: float, key: tuple) -> tuple[bool, bool]:
    """(run phase work, run the sweep). False/False means this cycle needs no database at all."""
    changed = _state["key"] != key or not _state["bootstrapped"]
    sweep = now_ts - _state["sweep"] >= SWEEP_SECONDS or not _state["bootstrapped"]
    return changed, sweep


async def _announce_and_post(db, deps: svc.Deps, round_date, now, events: list[dict]) -> None:
    announced = await svc.announce_round(db, deps, round_date, now)
    if not announced:
        return
    body = await svc.winner_post_text(db, announced)
    await svc.post_once(db, deps, "winner" if announced["kind"] == "winner" else "no_launch", announced["round_date"].isoformat(), body)
    if announced["kind"] == "winner":
        events.append({"gf_winner": {"round_date": round_date.isoformat(), "idea_id": announced["idea"]["idea_id"],
                                     "name": announced["idea"]["name"], "ticker": announced["idea"]["ticker"]}})
    events.append({"gf_phase": {"round_date": round_date.isoformat(), "phase": "announced"}})


async def advance_rounds(db, deps: svc.Deps, now: datetime, events: list[dict]) -> None:
    sch = deps.schedule
    rd = gf_rounds.round_date_at(now, sch)
    phase = gf_rounds.phase_at(now, sch)

    # a round the worker slept through is finished first, in order
    for old in await gf_store.open_rounds_before(db, rd):
        await svc.score_round(db, deps, old["round_date"], now)
        await _announce_and_post(db, deps, old["round_date"], now, events)
    for old in await gf_store.recent_rounds(db, 3):
        if old["round_date"] < rd and old["status"] == "scoring":
            await _announce_and_post(db, deps, old["round_date"], now, events)

    rnd = await gf_store.ensure_round(db, rd)
    if phase == "vote" and rnd["status"] == "submit":
        await gf_store.set_round(db, rd, status="vote")
        await db.commit()
        events.append({"gf_phase": {"round_date": rd.isoformat(), "phase": "vote"}})
    if phase in ("scoring", "announced") and rnd["status"] in ("submit", "review", "vote"):
        if await svc.score_round(db, deps, rd, now):
            events.append({"gf_phase": {"round_date": rd.isoformat(), "phase": "scoring"}})
    if phase == "announced":
        await _announce_and_post(db, deps, rd, now, events)


async def sweep(db, deps: svc.Deps, now: datetime, events: list[dict]) -> None:
    for v in await svc.sync_verdicts(db, deps):
        body = gf_poster.render_verdict_post(name=v["name"], ticker=v["ticker"], verdict=v["verdict"],
                                             peak_mc_usd=float(v["peak_mc_usd"]) if v["peak_mc_usd"] is not None else None,
                                             fees_to_creator_eth=v["fees_to_creator"], epc_burned=v["epc_burned"])
        await svc.post_once(db, deps, "verdict", v["idea_id"], body)
        events.append({"gf_verdict": {"idea_id": v["idea_id"], "verdict": v["verdict"]}})
    await svc.index_distributions(db, deps)
    await svc.keeper_distribute(db, deps, now)
    for l in await gf_store.launches_missing_post(db, "live"):
        pons = settings.GF_PONS_URL_TEMPLATE.format(ca=l["ca"]) if settings.GF_PONS_URL_TEMPLATE else None
        body = gf_poster.render_live_post(name=l["name"], ticker=l["ticker"], ca=l["ca"], pons_url=pons,
                                          blockscout_url=f"{settings.BLOCKSCOUT_BASE.rstrip('/')}/token/{l['ca']}")
        await svc.post_once(db, deps, "live", l["idea_id"], body)
    await svc.retry_failed_posts(db, deps)
    await gf_store.purge_stale(db)
    await db.commit()


async def bootstrap(db, deps: svc.Deps) -> None:
    """Re-register every community launch so the watcher tracks it and Golem's trading excludes it after a restart."""
    for e in await svc.load_registered_entries(db):
        goforge_config.register_runtime_entry(e)


async def run_gf_cycle(deps: Optional[svc.Deps] = None, now: Optional[datetime] = None) -> bool:
    """One cycle. Returns True when it did any database work."""
    now = now or datetime.now(timezone.utc)
    deps = deps or _default_deps()
    sch = deps.schedule
    key = (gf_rounds.round_date_at(now, sch), gf_rounds.phase_at(now, sch))
    changed, do_sweep = due(time.time(), key)
    if not changed and not do_sweep:
        return False
    events: list[dict] = []
    async with exclusive(GF_WORKER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            return False
        try:
            if not _state["bootstrapped"]:
                await bootstrap(db, deps)
            await advance_rounds(db, deps, now, events)
            if do_sweep:
                await sweep(db, deps, now, events)
                _state["sweep"] = time.time()
            _state["key"] = key
            _state["bootstrapped"] = True
        finally:
            await db.rollback()
    for ev in events:
        await manager.broadcast(ev)
    if events:
        from app.api import gf_endpoints
        gf_endpoints.invalidate_cache()
    return True


def _default_deps() -> svc.Deps:
    from app.api.gf_endpoints import get_deps
    return get_deps()


async def start_gf_worker_loop():
    if not settings.GF_ENABLED:
        print("[GF WORKER] Disabled via GF_ENABLED.")
        return
    schema_ready = False
    print(f"[GF WORKER] Started (interval {settings.GF_WORKER_INTERVAL_SECONDS}s).")
    while True:
        try:
            if not schema_ready:
                async with AsyncSessionLocal() as db:
                    await ensure_goforge_schema(db)
                    await ensure_gf_schema(db)
                schema_ready = True
            await run_gf_cycle()
        except Exception as e:
            print(f"[GF WORKER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(settings.GF_WORKER_INTERVAL_SECONDS)
