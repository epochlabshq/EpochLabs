import asyncio
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import AsyncSessionLocal, get_db
from app.api.endpoints import get_latest_model, serialize_model_run
from app.api.epochs_endpoints import load_completed
from app.services import desk_chain
from app.services.desk_pinned import load_pinned_rows
from app.services.desk_paper import load_paper_trades, serialize_paper
from app.services.desk import (
    Decision, anonymize_waiting, pinned_watching_rows, derive_state, gated_reason, group_trades, heartbeat, revealed_drops,
    serialize_closed, serialize_open, totals, watching_rows, WAITING_STAGES, WEI,
)
from app.services.golem_guard import trade_gate

router = APIRouter(prefix="/api")

CACHE_TTL_SECONDS = 60.0
STALE_MAX_SECONDS = 300.0
_cache: Optional["DeskInputs"] = None
_cache_ts = 0.0
_refreshing = False


def invalidate_desk_cache() -> None:
    """Mark the snapshot stale (it is still served once while a fresh one loads)."""
    global _cache_ts
    _cache_ts = 0.0


@dataclass
class DeskInputs:
    """Everything the Desk is built from, loaded in one place. Chain fields are None when the RPC failed."""
    epoch2_complete: bool
    paused_reason: Optional[str]
    model: Optional[dict]
    desk_state: dict
    watching_tokens: list[dict]
    candidates: list[dict]
    revealed: list[dict]
    swaps: list[dict]
    decisions: dict[str, Decision]
    meta: dict[str, dict]
    burned_wei: int
    watching_not_onchain: int = 0
    watching_no_price: int = 0
    watching_below_min: int = 0
    watching_below_threshold: int = 0
    pinned: list[dict] = field(default_factory=list)
    paper_trades: list[dict] = field(default_factory=list)
    paper_marks: dict[str, dict] = field(default_factory=dict)
    balance_wei: Optional[int] = None
    marks: dict[str, tuple[int, int]] = field(default_factory=dict)
    decimals: dict[str, Optional[int]] = field(default_factory=dict)


def _blockscout() -> str:
    return settings.BLOCKSCOUT_BASE.rstrip("/")


def _addr(address: str) -> dict:
    return {"address": address, "url": f"{_blockscout()}/address/{address}"} if address else {"address": None, "url": None}


def build_trade_views(inp: DeskInputs) -> tuple[list[dict], list[dict]]:
    """(open, closed) serialized trades, newest first; closed is complete (not truncated)."""
    trades = group_trades(inp.swaps)
    bs = _blockscout()
    open_rows = [
        serialize_open(t, meta=inp.meta, decisions=inp.decisions, mark=inp.marks.get(t.token),
                       decimals=inp.decimals.get(t.token), blockscout=bs)
        for t in trades if not t.closed
    ]
    closed_rows = [
        serialize_closed(t, meta=inp.meta, decisions=inp.decisions, decimals=inp.decimals.get(t.token), blockscout=bs)
        for t in trades if t.closed
    ]
    open_rows.sort(key=lambda r: r["entry"]["at"], reverse=True)
    closed_rows.sort(key=lambda r: r["exit"]["at"], reverse=True)
    return open_rows, closed_rows


def build_desk_payload(inp: DeskInputs, now: datetime) -> dict:
    """GET /api/desk body. The only path by which Desk data is serialized (REST and WS alike)."""
    open_rows, closed_rows = build_trade_views(inp)
    waiting = anonymize_waiting(inp.candidates, now, settings.DESK_DROPPED_VISIBLE_H)
    state = derive_state(inp.epoch2_complete, inp.paused_reason, len(open_rows),
                         [c["stage"] for c in inp.candidates if c["stage"] in WAITING_STAGES])
    if state == "gated":
        blocked_by = gated_reason(inp.model, settings.AUC_TARGET)
    elif state == "paused":
        blocked_by = inp.paused_reason
    else:
        blocked_by = None
    hb = heartbeat(inp.desk_state.get("last_decision_at"), now, settings.DESK_HEARTBEAT_WARN_SECONDS)
    burn = settings.EPC_BURN_ADDRESS
    return {
        "state": state,
        "blocked_by": blocked_by,
        "last_decision_at": hb["last_decision_at"],
        "heartbeat": hb,
        "threshold": settings.DESK_ENTRY_THRESHOLD,
        "model": {"run_id": inp.model["run_id"], "proven_floor": inp.model["proven_floor"]} if inp.model else None,
        "wallet": {
            **_addr(settings.GOLEM_WALLET),
            "eth": None if inp.balance_wei is None else inp.balance_wei / WEI,
            "start_eth": settings.DESK_START_ETH,
        },
        "agent": _addr(settings.GOLEM_AGENT),
        "pnl": totals(open_rows, closed_rows, settings.DESK_START_ETH),
        "epc_burned": {
            "amount": inp.burned_wei / WEI,
            "amount_wei": str(inp.burned_wei),
            "usd": None,  # no onchain EPC/USD source yet
            "burn_address": _addr(burn) if burn else None,
        },
        # Scored candidates first (capped), then the pinned tokens at the bottom
        "watching": watching_rows(inp.watching_tokens, settings.DESK_ENTRY_THRESHOLD,
                                  settings.desk_excluded_tokens, settings.DESK_WATCHING_LIMIT,
                                  take_profit_mc_usd=settings.DESK_TP_MC_USD) + pinned_watching_rows(inp.pinned),
        "watching_not_onchain": inp.watching_not_onchain,
        "watching_no_price": inp.watching_no_price,
        "watching_below_min": inp.watching_below_min,
        "watching_below_threshold": inp.watching_below_threshold,
        "watch_min_mc_usd": settings.DESK_WATCH_MIN_MC_USD,
        "take_profit_mc_usd": settings.DESK_TP_MC_USD,
        "stop_loss_mc_usd": settings.DESK_SL_MC_USD,
        "max_hold_h": settings.DESK_MAX_HOLD_H,
        "waiting": waiting,
        "dropped_revealed": [
            {**d, "token": {**_addr(d["token"]), **{k: v for k, v in (inp.meta.get(d["token"].lower()) or {}).items()
                                                    if k in ("name", "symbol")}}}
            for d in inp.revealed
        ],
        "open": open_rows,
        "closed": closed_rows[:settings.DESK_CLOSED_LIMIT],
        "closed_total": len(closed_rows),
        # SIMULATION: hypothetical fills at real DexScreener prices. Never counted in wallet or PnL above.
        "simulation": {
            "enabled": settings.DESK_PAPER_ENABLED,
            "trades": serialize_paper(inp.paper_trades, inp.paper_marks)[::-1],
        },
        "generated_at": now.isoformat(),
    }


def find_trade(inp: DeskInputs, trade_id: str) -> Optional[dict]:
    trades = {t.id: t for t in group_trades(inp.swaps)}
    t = trades.get(trade_id)
    if t is None:
        return None
    bs = _blockscout()
    if t.closed:
        return {"status": "closed", **serialize_closed(
            t, meta=inp.meta, decisions=inp.decisions, decimals=inp.decimals.get(t.token), blockscout=bs,
            include_why=True)}
    return {"status": "open", **serialize_open(
        t, meta=inp.meta, decisions=inp.decisions, mark=inp.marks.get(t.token),
        decimals=inp.decimals.get(t.token), blockscout=bs)}


async def load_desk_inputs(db: AsyncSession, with_chain: bool = True) -> DeskInputs:
    completed = await load_completed(db)
    latest = await get_latest_model(db)
    model = serialize_model_run(latest) if latest else None
    gate = trade_gate(completed, model, settings.AUC_TARGET)
    epoch2 = 2 in completed

    st = (await db.execute(text(
        "SELECT state, changed_at, last_decision_at FROM desk_state WHERE id = 1"
    ))).mappings().first()
    # Watching: the public ingest feed (crossed $10K, not yet labeled) with scores from the current run only
    watching = (await db.execute(text(
        "SELECT t.mint, t.name, t.symbol, GREATEST(t.peak_mc::float, dm.peak_seen_usd) AS peak_mc, "
        "dm.mc_usd AS mc_now, dm.pair_url, dm.fetched_at AS mc_at, t.launched_at, "
        "COALESCE(t.holders, lh.holders) AS holders, lh.sampled_at AS holders_sampled_at, s.survival "
        "FROM tokens t JOIN desk_market dm ON dm.mint = t.mint AND dm.mc_usd >= :lo AND dm.mc_usd < :hi "
        # A pool with no real liquidity has a market cap on paper only: Golem could never trade it
        "AND dm.liq_usd >= :minliq "
        "LEFT JOIN desk_scores s ON s.mint = t.mint AND s.run_id = :run "
        "LEFT JOIN desk_live_holders lh ON lh.mint = t.mint "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' "
        "AND t.mint ~ '^0x[0-9a-fA-F]{40}$' AND COALESCE(lh.has_code, true) "
        "AND t.launched_at > now() - make_interval(hours => :h) "
        # A scored token under the entry threshold is dropped from the list so the slot goes to the next one
        "AND (s.survival IS NULL OR s.survival >= :thr) "
        "ORDER BY s.survival DESC NULLS LAST, dm.mc_usd DESC LIMIT :lim"
    ), {"run": model["run_id"] if model else -1, "h": settings.DESK_MAX_HOLD_H, "thr": settings.DESK_ENTRY_THRESHOLD,
        "lim": settings.DESK_WATCHING_LIMIT, "lo": settings.DESK_WATCH_MIN_MC_USD,
        "hi": settings.DESK_TP_MC_USD, "minliq": settings.DESK_MIN_LIQ_USD})).mappings().all()
    below_threshold = (await db.execute(text(
        "SELECT count(*) FROM desk_scores s JOIN desk_market dm ON dm.mint = s.mint AND dm.mc_usd >= :lo "
        "WHERE s.run_id = :run AND s.survival < :thr"
    ), {"run": model["run_id"] if model else -1, "thr": settings.DESK_ENTRY_THRESHOLD,
        "lo": settings.DESK_WATCH_MIN_MC_USD})).scalar()
    # Watched rows left out of the list right now, by reason
    hidden = (await db.execute(text(
        "SELECT count(*) FILTER (WHERE dm.mint IS NULL OR dm.mc_usd IS NULL) AS no_price, "
        "count(*) FILTER (WHERE dm.mc_usd < :lo) AS below_min "
        "FROM tokens t JOIN desk_live_holders lh ON lh.mint = t.mint AND lh.has_code "
        "LEFT JOIN desk_market dm ON dm.mint = t.mint "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' "
        "AND t.launched_at > now() - make_interval(hours => :h)"
    ), {"h": settings.DESK_MAX_HOLD_H, "lo": settings.DESK_WATCH_MIN_MC_USD})).mappings().one()
    # Feed rows that are not Robinhood Chain contracts (other chains' addresses, NEAR names): counted, not shown
    not_onchain = (await db.execute(text(
        "SELECT count(*) FROM tokens t LEFT JOIN desk_live_holders lh ON lh.mint = t.mint "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' "
        "AND t.launched_at > now() - make_interval(hours => :h) "
        "AND (t.mint !~ '^0x[0-9a-fA-F]{40}$' OR lh.has_code = false)"
    ), {"h": settings.DESK_MAX_HOLD_H})).scalar()
    candidates = (await db.execute(text(
        "SELECT id, token, queued_at, survival, stage, dropped_reason, dropped_at FROM desk_candidates "
        "WHERE stage = ANY(:stages) OR (stage = 'dropped' AND dropped_at > now() - make_interval(hours => :h))"
    ), {"stages": list(WAITING_STAGES), "h": settings.DESK_DROPPED_VISIBLE_H})).mappings().all()
    revealed = (await db.execute(text(
        "SELECT id, token, queued_at, survival, stage, dropped_reason, dropped_at FROM desk_candidates "
        "WHERE stage = 'dropped' AND dropped_at <= now() - make_interval(hours => :h) "
        "ORDER BY dropped_at DESC LIMIT 20"
    ), {"h": settings.DESK_DROPPED_REVEAL_H})).mappings().all()
    swaps = (await db.execute(text(
        "SELECT tx_hash, block, at, side, token, token_amount_wei, eth_amount_wei FROM golem_swaps"
    ))).mappings().all()
    decisions = (await db.execute(text(
        "SELECT id, side, tx_hash, why_canonical, why_sha256, exit_reason FROM golem_trade_decisions "
        "WHERE tx_hash IS NOT NULL"
    ))).mappings().all()
    burned = (await db.execute(text(
        "SELECT COALESCE(SUM(amount_wei), 0)::text FROM epc_burns WHERE burn_address = :b"
    ), {"b": settings.EPC_BURN_ADDRESS.lower()})).scalar()

    trade_tokens = sorted({(s["token"] or "").lower() for s in swaps if s["token"]})
    meta_tokens = trade_tokens + [r["token"].lower() for r in revealed]
    meta_rows = (await db.execute(text(
        "SELECT lower(mint) AS mint, name, symbol, status::text AS status FROM tokens WHERE lower(mint) = ANY(:m)"
    ), {"m": meta_tokens})).mappings().all() if meta_tokens else []

    inp = DeskInputs(
        epoch2_complete=epoch2,
        paused_reason=gate.reason if epoch2 and not gate.allowed else None,
        model=model,
        desk_state=dict(st) if st else {},
        watching_tokens=[dict(r) for r in watching],
        candidates=[dict(r) for r in candidates],
        revealed=revealed_drops([dict(r) for r in revealed], datetime.now(timezone.utc),
                                settings.DESK_DROPPED_REVEAL_H),
        swaps=[dict(s) for s in swaps],
        decisions={d["tx_hash"]: Decision(d["id"], d["side"], d["why_canonical"], d["why_sha256"], d["exit_reason"])
                   for d in decisions},
        meta={m["mint"]: {"name": m["name"], "symbol": m["symbol"], "status": m["status"]} for m in meta_rows},
        burned_wei=int(burned or 0),
        watching_not_onchain=int(not_onchain or 0),
        watching_no_price=int(hidden["no_price"] or 0),
        watching_below_min=int(hidden["below_min"] or 0),
        watching_below_threshold=int(below_threshold or 0),
    )
    inp.pinned = await load_pinned_rows(db)
    if settings.DESK_PAPER_ENABLED:
        inp.paper_trades = await load_paper_trades(db)
        open_mints = [p["mint"] for p in inp.paper_trades if p["exit_at"] is None]
        if open_mints:
            rows = (await db.execute(text(
                "SELECT mint, price_usd, mc_usd FROM desk_market WHERE mint = ANY(:m)"
            ), {"m": open_mints})).mappings().all()
            inp.paper_marks = {r["mint"]: dict(r) for r in rows}
    if with_chain:
        r = await desk_chain.reader()
        if r is not None:
            open_tokens = [t.token for t in group_trades(inp.swaps) if not t.closed]
            inp.balance_wei = await desk_chain.wallet_balance_wei(r, settings.GOLEM_WALLET)
            inp.marks = await desk_chain.marks(r, open_tokens)
            inp.decimals = await desk_chain.decimals(r, trade_tokens)
    return inp


async def _refresh_cache() -> DeskInputs:
    global _cache, _cache_ts
    async with AsyncSessionLocal() as db:
        inp = await load_desk_inputs(db)
    _cache, _cache_ts = inp, time.time()
    return inp


async def _refresh_in_background() -> None:
    global _refreshing
    try:
        await _refresh_cache()
    except Exception as e:
        print(f"[API ERROR] desk background refresh failed: {e}", flush=True)
    finally:
        _refreshing = False


async def _cached_inputs(db: AsyncSession) -> DeskInputs:
    """
    Stale-while-revalidate: a read costs ~10 sequential queries to the remote database plus chain reads (5-7s),
    so a slightly old snapshot is served at once while a fresh one loads. Never older than STALE_MAX_SECONDS.
    """
    global _cache, _cache_ts, _refreshing
    age = time.time() - _cache_ts
    if _cache is not None and age < CACHE_TTL_SECONDS:
        return _cache
    if _cache is not None and age < STALE_MAX_SECONDS:
        if not _refreshing:
            _refreshing = True
            asyncio.create_task(_refresh_in_background())
        return _cache
    try:
        inp = await load_desk_inputs(db)
    except Exception as e:
        print(f"[API ERROR] desk failed: {e}\n{traceback.format_exc()}", flush=True)
        # Never fall back to invented numbers
        raise HTTPException(status_code=503, detail="Desk data unavailable")
    _cache, _cache_ts = inp, time.time()
    return inp


@router.get("/desk")
async def get_desk(db: AsyncSession = Depends(get_db)):
    """GET /api/desk - The Desk snapshot: state, Watching, anonymous Waiting slots, open and closed trades."""
    return build_desk_payload(await _cached_inputs(db), datetime.now(timezone.utc))


@router.get("/desk/trades/{trade_id}")
async def get_desk_trade(trade_id: str, db: AsyncSession = Depends(get_db)):
    """GET /api/desk/trades/{id} - One confirmed trade with its full Why card."""
    trade = find_trade(await _cached_inputs(db), trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade
