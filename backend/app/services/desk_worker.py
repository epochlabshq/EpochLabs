"""
Desk worker: every DESK_WORKER_INTERVAL_SECONDS
1. score the Watching feed with the latest model run (desk_scores) and beat the heartbeat,
2. mark candidates whose entry tx confirmed as `open`,
3. announce newly confirmed opens/closes once (desk_open / desk_close),
4. broadcast desk_state / desk_waiting when they change.
Every WS payload is built by the same serializer as GET /api/desk, so nothing leaks through the stream.
"""
import asyncio
import hashlib
import json
import traceback
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.desk_schema import ensure_desk_schema
from app.db.epochs_schema import ensure_epochs_schema
from app.db.locks import exclusive
from app.api.desk_endpoints import build_desk_payload, find_trade, invalidate_desk_cache, load_desk_inputs
from app.api.websocket import manager
from app.services.desk import group_trades
from app.services.desk_executor import run_executor_cycle
from app.services.desk_discovery import discover_tokens
from app.services.desk_paper import run_paper_cycle
from app.services.desk_poster import initial_post_status, post_due
from app.services import desk_chain
from app.services.live_holders import refresh_live_holders
from app.services.live_market import refresh_market
from app.services.scorer import ScorerUnavailable, score_mints

DESK_WORKER_LOCK_KEY = 0x45504F43_4445534B  # "EPOC" "DESK"

_last_waiting_digest: Optional[str] = None


async def score_watching(db) -> int:
    """Score every token currently in the Watching feed. Returns how many were scored."""
    # Only tokens that exist on Robinhood Chain (feed rows from other chains are never scored)
    mints = [r[0] for r in (await db.execute(text(
        "SELECT t.mint FROM tokens t JOIN desk_live_holders lh ON lh.mint = t.mint AND lh.has_code "
        "JOIN desk_market dm ON dm.mint = t.mint AND dm.mc_usd >= :lo AND dm.mc_usd < :hi AND dm.liq_usd >= :liq "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' "
        "AND t.launched_at > now() - make_interval(hours => :h)"
    ), {"h": settings.DESK_MAX_HOLD_H, "lo": settings.DESK_WATCH_MIN_MC_USD,
        "hi": settings.DESK_TP_MC_USD, "liq": settings.DESK_MIN_LIQ_USD})).all()]
    if not mints:
        return 0
    scores = await score_mints(db, mints)
    # Drop scores for tokens that left the feed or can no longer be scored (never show a stale number)
    await db.execute(text("DELETE FROM desk_scores WHERE NOT (mint = ANY(:m))"), {"m": list(scores)})
    for mint, s in scores.items():
        await db.execute(text(
            "INSERT INTO desk_scores (mint, run_id, survival, top_signals, scored_at) "
            "VALUES (:m, :r, :s, CAST(:sig AS JSONB), :at) "
            "ON CONFLICT (mint) DO UPDATE SET run_id = EXCLUDED.run_id, survival = EXCLUDED.survival, "
            "top_signals = EXCLUDED.top_signals, scored_at = EXCLUDED.scored_at"
        ), {"m": mint, "r": s["run_id"], "s": s["survival"], "sig": json.dumps(s["top_signals"]),
            "at": datetime.fromisoformat(s["scored_at"])})
    await db.commit()
    return len(scores)


async def mark_confirmed_candidates(db) -> None:
    """A candidate leaves Waiting only once its entry swap is indexed (i.e. the tx confirmed)."""
    await db.execute(text(
        "UPDATE desk_candidates c SET stage = 'open' FROM golem_trade_decisions d "
        "JOIN golem_swaps s ON s.tx_hash = d.tx_hash "
        "WHERE c.decision_id = d.id AND d.side = 'buy' AND c.stage IN ('liquidity_check', 'sizing', 'entering')"
    ))
    await db.commit()


async def announce_trades(db, inp) -> list[dict]:
    """Insert each newly opened/closed trade into desk_announcements once; returns WS events to send."""
    events = []
    now = datetime.now(timezone.utc)
    for t in group_trades(inp.swaps):
        kinds = [("open", t.entry)] + ([("close", t.exit)] if t.closed else [])
        for kind, fill in kinds:
            new = (await db.execute(text(
                "INSERT INTO desk_announcements (trade_id, kind, tx_hash, event_at, post_status) "
                "VALUES (:id, :k, :tx, :at, :ps) ON CONFLICT DO NOTHING RETURNING trade_id"
            ), {"id": t.id, "k": kind, "tx": fill.tx_hash, "at": fill.at,
                "ps": initial_post_status(fill.at, now, settings.DESK_X_POST_MAX_AGE_H)})).first()
            if new:
                events.append({f"desk_{kind}": find_trade(inp, t.id)})
    await db.commit()
    return events


async def run_desk_cycle() -> None:
    global _last_waiting_digest
    events: list[dict] = []
    async with exclusive(DESK_WORKER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            return
        try:
            now = datetime.now(timezone.utc)
            try:
                added = await discover_tokens(db)
                print(f"[DESK WORKER] Discovered {added} new tokens on DexScreener", flush=True)
            except Exception as e:
                await db.rollback()
                print(f"[DESK WORKER] Discovery failed: {type(e).__name__} {e}", flush=True)
            try:
                updated = await refresh_live_holders(
                    db, await desk_chain.log_reader(), await desk_chain.transfers_reader(),
                    max_age_h=settings.DESK_MAX_HOLD_H, limit=settings.DESK_HOLDERS_BATCH,
                    code_r=await desk_chain.reader())
                print(f"[DESK WORKER] Counted holders onchain for {updated} tokens", flush=True)
            except Exception as e:
                await db.rollback()
                print(f"[DESK WORKER] Holder count failed: {type(e).__name__} {e}", flush=True)
            try:
                priced = await refresh_market(db, max_age_h=settings.DESK_MAX_HOLD_H)
                print(f"[DESK WORKER] Live market cap for {priced} tokens", flush=True)
            except Exception as e:
                await db.rollback()
                print(f"[DESK WORKER] Market refresh failed: {type(e).__name__} {e}", flush=True)
            try:
                n = await score_watching(db)
                # A scoring pass over the feed is a decision cycle: that is what the heartbeat reports
                await db.execute(text("UPDATE desk_state SET last_decision_at = :now WHERE id = 1"), {"now": now})
                await db.commit()
                print(f"[DESK WORKER] Scored {n} watched tokens", flush=True)
            except ScorerUnavailable as e:
                await db.rollback()
                print(f"[DESK WORKER] Scoring unavailable: {e}", flush=True)

            if settings.DESK_PAPER_ENABLED:
                try:
                    for line in await run_paper_cycle(db):
                        print(f"[DESK PAPER] {line}", flush=True)
                except Exception as e:
                    await db.rollback()
                    print(f"[DESK PAPER] Cycle failed: {type(e).__name__} {e}", flush=True)

            await mark_confirmed_candidates(db)
            inp = await load_desk_inputs(db)
            if settings.DESK_EXECUTOR_MODE != "off":
                try:
                    await run_executor_cycle(db, inp)
                except Exception as e:
                    # Never let an executor error stop scoring, the heartbeat or the announcements
                    await db.rollback()
                    print(f"[DESK EXECUTOR] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
                inp = await load_desk_inputs(db)
            events += await announce_trades(db, inp)
            if settings.DESK_X_POST_ENABLED:
                from app.services.twitter_service import twitter_service
                await post_due(db, inp, twitter_service)

            payload = build_desk_payload(inp, now)
            prev = inp.desk_state.get("state")
            if payload["state"] != prev:
                await db.execute(text(
                    "UPDATE desk_state SET state = :s, changed_at = :now WHERE id = 1"
                ), {"s": payload["state"], "now": now})
                await db.commit()
                events.append({"desk_state": {
                    "state": payload["state"], "previous": prev, "blocked_by": payload["blocked_by"],
                    "changed_at": now.isoformat(),
                }})
            digest = hashlib.sha256(json.dumps(payload["waiting"], sort_keys=True).encode()).hexdigest()
            if digest != _last_waiting_digest:
                if _last_waiting_digest is not None:
                    events.append({"desk_waiting": {"waiting": payload["waiting"]}})
                _last_waiting_digest = digest
        finally:
            await db.rollback()

    invalidate_desk_cache()
    for ev in events:
        await manager.broadcast(ev)


async def start_desk_worker_loop():
    if not settings.DESK_WORKER_ENABLED:
        print("[DESK WORKER] Disabled via DESK_WORKER_ENABLED.")
        return
    schema_ready = False
    print(f"[DESK WORKER] Started (interval {settings.DESK_WORKER_INTERVAL_SECONDS}s).")
    while True:
        try:
            if not schema_ready:
                # Desk tables extend the Epochs schema (golem_trade_decisions), so apply that first
                async with AsyncSessionLocal() as db:
                    await ensure_epochs_schema(db)
                    await ensure_desk_schema(db)
                schema_ready = True
            await run_desk_cycle()
        except Exception as e:
            print(f"[DESK WORKER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(settings.DESK_WORKER_INTERVAL_SECONDS)
