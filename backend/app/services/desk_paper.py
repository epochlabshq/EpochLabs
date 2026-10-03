"""
Paper trading for The Desk (SIMULATION). No wallet, no signer, no transaction: Golem's entry and exit rules are
run on live DexScreener prices and every hypothetical fill is logged with its real price and time.
Independent of the live trade gate on purpose, so it can run while Golem is gated. Rows live in
desk_paper_trades and are always labeled as simulation by the API and the page.

Entries: tokens in the Watching feed scored at or above the entry threshold, best survival first, never a team
token, never one already traded, up to DESK_MAX_OPEN open at once. Exits: the same rule as the live executor
(take-profit, stop-loss, time limit), checked on every Desk cycle.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.core.config import settings
from app.services.desk_trading import exit_reason, select_candidates


def pnl_pct(entry_price: Optional[float], exit_price: Optional[float]) -> Optional[float]:
    if not entry_price or exit_price is None:
        return None
    return round((exit_price / entry_price - 1) * 100, 4)


async def run_paper_cycle(db) -> list[str]:
    log: list[str] = []
    now = datetime.now(timezone.utc)

    # Exits first, so a freed slot can be reused in the same cycle
    open_rows = (await db.execute(text(
        "SELECT p.id, p.mint, p.entry_at, dm.mc_usd, dm.price_usd, dm.fetched_at "
        "FROM desk_paper_trades p LEFT JOIN desk_market dm ON dm.mint = p.mint WHERE p.exit_at IS NULL"
    ))).mappings().all()
    for p in open_rows:
        fresh = p["mc_usd"] is not None and p["price_usd"] is not None
        reason = exit_reason(mc_now=p["mc_usd"] if fresh else None, entered_at=p["entry_at"], now=now,
                             take_profit_mc_usd=settings.DESK_TP_MC_USD, stop_loss_mc_usd=settings.DESK_SL_MC_USD,
                             max_hold_h=settings.DESK_MAX_HOLD_H)
        if reason is None:
            continue
        await db.execute(text(
            "UPDATE desk_paper_trades SET exit_at = :at, exit_mc_usd = :mc, exit_price_usd = :px, exit_reason = :r "
            "WHERE id = :id"
        ), {"at": p["fetched_at"] if fresh else now, "mc": p["mc_usd"] if fresh else None,
            "px": p["price_usd"] if fresh else None, "r": reason, "id": p["id"]})
        log.append(f"paper SELL {p['mint']} ({reason})")
    await db.commit()

    # Entries
    n_open = (await db.execute(text("SELECT count(*) FROM desk_paper_trades WHERE exit_at IS NULL"))).scalar()
    traded = {r[0].lower() for r in (await db.execute(text("SELECT mint FROM desk_paper_trades"))).all()}
    scored = [dict(r) for r in (await db.execute(text(
        "SELECT s.mint, s.survival, t.name, t.symbol, dm.mc_usd, dm.price_usd, dm.fetched_at "
        "FROM desk_scores s JOIN tokens t ON t.mint = s.mint JOIN desk_market dm ON dm.mint = s.mint "
        "WHERE dm.price_usd > 0 AND dm.mc_usd >= :lo AND dm.mc_usd < :hi AND dm.liq_usd >= :liq"
    ), {"lo": settings.DESK_WATCH_MIN_MC_USD, "hi": settings.DESK_TP_MC_USD, "liq": settings.DESK_MIN_LIQ_USD})).mappings().all()]
    picks = select_candidates(scored, threshold=settings.DESK_ENTRY_THRESHOLD, excluded=settings.desk_excluded_tokens,
                              busy=traded, open_count=n_open, queued_count=0, max_open=settings.DESK_MAX_OPEN)
    by_mint = {s["mint"]: s for s in scored}
    for pick in picks:
        s = by_mint[pick["mint"]]
        await db.execute(text(
            "INSERT INTO desk_paper_trades (mint, name, symbol, survival, entry_at, entry_mc_usd, entry_price_usd) "
            "VALUES (:m, :n, :sy, :sv, :at, :mc, :px)"
        ), {"m": s["mint"], "n": s["name"], "sy": s["symbol"], "sv": s["survival"], "at": s["fetched_at"],
            "mc": s["mc_usd"], "px": s["price_usd"]})
        log.append(f"paper BUY {s['symbol'] or s['mint']} @ ${s['price_usd']:.8f} (survival {s['survival']:.3f})")
    await db.commit()
    return log


async def load_paper_trades(db) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT id, mint, name, symbol, survival, entry_at, entry_mc_usd, entry_price_usd, exit_at, exit_mc_usd, "
        "exit_price_usd, exit_reason FROM desk_paper_trades ORDER BY id"
    ))).mappings().all()
    return [dict(r) for r in rows]


def serialize_paper(rows: list[dict], marks: dict[str, dict]) -> list[dict]:
    """marks: mint -> {price_usd, mc_usd} for open rows, to show the live paper PnL."""
    out = []
    for r in rows:
        closed = r["exit_at"] is not None
        mark = marks.get(r["mint"]) or {}
        out.append({
            "id": f"p_{r['id']:04d}",
            "token": {"name": r["name"], "symbol": r["symbol"], "address": r["mint"],
                      "dexscreener_url": f"https://dexscreener.com/robinhood/{r['mint'].lower()}"},
            "survival": round(r["survival"], 4),
            "entry": {"at": r["entry_at"].isoformat(), "price_usd": r["entry_price_usd"], "mc_usd": r["entry_mc_usd"]},
            "exit": {"at": r["exit_at"].isoformat(), "price_usd": r["exit_price_usd"], "mc_usd": r["exit_mc_usd"]}
            if closed else None,
            "exit_reason": r["exit_reason"],
            "mark_price_usd": None if closed else mark.get("price_usd"),
            "pnl_pct": pnl_pct(r["entry_price_usd"], r["exit_price_usd"] if closed else mark.get("price_usd")),
        })
    return out
