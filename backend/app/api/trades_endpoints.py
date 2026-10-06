import time
import traceback

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.services.golem_guard import current_trade_gate
from app.services.trade_journal import TradeRow, compute_results

router = APIRouter(prefix="/api")

_cache = None
_cache_ts = 0.0
CACHE_TTL_SECONDS = 300.0

_int = lambda v: int(v) if v is not None else None
_str = lambda v: str(v) if v is not None else None  # wei can exceed JS safe integers


def build_trades_payload(swaps: list[dict], decisions: dict[str, dict]) -> dict:
    rows = [TradeRow(s["tx_hash"], s["block"], s["side"] or "swap", s["token"],
                     _int(s["token_amount_wei"]), _int(s["eth_amount_wei"])) for s in swaps]
    results = compute_results(rows)
    bs = settings.BLOCKSCOUT_BASE.rstrip("/")
    trades = []
    for s in sorted(swaps, key=lambda r: r["block"], reverse=True):
        d = decisions.get(s["tx_hash"])
        r = results[s["tx_hash"]]
        trades.append({
            "tx_hash": s["tx_hash"],
            "tx_url": f"{bs}/tx/{s['tx_hash']}",
            "at": s["at"].isoformat(),
            "side": s["side"] or "swap",
            "token": s["token"],
            "token_url": f"{bs}/address/{s['token']}" if s["token"] else None,
            "token_amount_wei": _str(s["token_amount_wei"]),
            "eth_amount_wei": _str(s["eth_amount_wei"]),
            "survival_probability": d["survival_probability"] if d else None,
            "top_signal": d["top_signal"] if d else None,
            "run_id": d["run_id"] if d else None,
            "result_wei": _str(r["result_wei"]),
            "partial_basis": r["partial_basis"],
        })
    realized = [t for t in results.values() if t["result_wei"] is not None]
    return {
        "trades": trades,
        "summary": {
            "count": len(trades),
            "closed": len(realized),
            "wins": sum(1 for t in realized if t["result_wei"] > 0),
            "losses": sum(1 for t in realized if t["result_wei"] < 0),
            "realized_wei": str(sum(t["result_wei"] for t in realized)),
        },
        "golem_wallet": settings.GOLEM_WALLET,
        "golem_wallet_url": f"{bs}/address/{settings.GOLEM_WALLET}",
    }


@router.get("/trades")
async def get_trades(db: AsyncSession = Depends(get_db)):
    """GET /api/trades - Trade Journal: every Golem swap (onchain) joined with the decision that produced it."""
    global _cache, _cache_ts
    if _cache is not None and time.time() - _cache_ts < CACHE_TTL_SECONDS:
        return _cache
    try:
        swaps = (await db.execute(text(
            "SELECT tx_hash, block, at, side, token, token_amount_wei, eth_amount_wei FROM golem_swaps"
        ))).mappings().all()
        decisions = (await db.execute(text(
            "SELECT tx_hash, survival_probability, top_signal, run_id FROM golem_trade_decisions WHERE tx_hash IS NOT NULL"
        ))).mappings().all()
        gate, _ = await current_trade_gate(db)
    except Exception as e:
        print(f"[API ERROR] get_trades failed: {e}\n{traceback.format_exc()}", flush=True)
        raise HTTPException(status_code=503, detail="Trade data unavailable")

    payload = build_trades_payload([dict(s) for s in swaps], {d["tx_hash"]: dict(d) for d in decisions})
    payload["can_trade"] = gate.allowed
    payload["paused_reason"] = gate.reason
    _cache, _cache_ts = payload, time.time()
    return payload


@router.get("/golem/status")
async def get_golem_status(db: AsyncSession = Depends(get_db)):
    """GET /api/golem/status - Whether Golem may trade right now, and why not."""
    try:
        gate, model = await current_trade_gate(db)
    except Exception:
        raise HTTPException(status_code=503, detail="Status unavailable")
    return {"can_trade": gate.allowed, "reason": gate.reason, "run_id": model["run_id"] if model else None}
