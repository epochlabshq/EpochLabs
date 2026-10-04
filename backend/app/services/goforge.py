"""
GoForge domain logic. Pure functions only (no I/O), so the verdict, the gate, visibility and the null-handling
are unit-testable.

Rules (Developer Brief: GoForge):
- Every number comes from the chain, Blockscout or DexScreener, never typed in. Only the CA, the launch tx and
  the Why card come from the config file.
- A launch is invisible (API, WS, HTML) until its pool is active: a leaked name or CA invites snipers.
- The 48h verdict uses the peak of the snapshots stored by the worker, and once locked it never changes.
- A missing value stays None. It is never replaced by 0.
- Golem judges its own token by the same rule it uses for every other token.
"""
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from app.core.goforge_config import LaunchEntry

VERDICTS = ("pending", "reached_30k", "stalled")
GATE_STATUSES = ("locked", "ready", "forging", "cooldown")
DEAD_ADDRESS = "0x000000000000000000000000000000000000dead"

# Fields the Why card carries; the API echoes them from the config, it never rewrites them
WHY_KEYS = ("window", "window_reason", "lore_summary", "model_run_id", "why_hash")


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def window_end(launched_at: datetime, hours: int) -> datetime:
    return launched_at + timedelta(hours=hours)


def in_window(ts: datetime, launched_at: datetime, hours: int) -> bool:
    """A snapshot counts toward the peak only if it was taken from the launch up to hour `hours` (inclusive)."""
    return launched_at <= ts <= window_end(launched_at, hours)


def next_peak(peak: Optional[float], mc_usd: Optional[float], ts: datetime, launched_at: datetime, hours: int,
              verdict: str) -> float:
    """The peak after a new snapshot: the max of the stored peak and an in-window market cap. Frozen once locked."""
    current = float(peak or 0)
    if verdict != "pending" or mc_usd is None or not in_window(ts, launched_at, hours):
        return current
    return max(current, float(mc_usd))


def decide_verdict(verdict: str, launched_at: datetime, now: datetime, peak: Optional[float], *,
                   hours: int, target_usd: float) -> Optional[str]:
    """
    The verdict to lock now, or None. None for a locked verdict (it can never change) and while the window is
    still open. Same rule Golem applies to every other token: peak >= target within the window.
    """
    if verdict != "pending":
        return None
    if now < window_end(launched_at, hours):
        return None
    return "reached_30k" if float(peak or 0) >= target_usd else "stalled"


def seconds_to_verdict(verdict: str, launched_at: Optional[datetime], now: datetime, hours: int) -> Optional[int]:
    if verdict != "pending" or launched_at is None:
        return None
    return max(0, int((window_end(launched_at, hours) - now).total_seconds()))


# ---------------------------------------------------------------------------
# Launch Gate
# ---------------------------------------------------------------------------

def evaluate_gate(*, closed_trades: int, min_trades: int, net_pnl_eth: Optional[float], model_fix_done: bool,
                  last_launch_at: Optional[datetime], cooldown_days: int, balance_eth: Optional[float],
                  reserve_eth: float, pending_launch: bool, now: datetime) -> dict:
    """
    The five conditions that must all hold before Golem launches again.
    status: forging while a launch is inside its 48h window; cooldown when the cooldown is the only thing
    left; ready when everything holds; locked otherwise.
    """
    cooldown_until = last_launch_at + timedelta(days=cooldown_days) if last_launch_at else None
    cooldown_ok = cooldown_until is None or now >= cooldown_until
    conditions = [
        {"key": "trading_proof", "label": f"{min_trades} real trades closed", "current": closed_trades,
         "target": min_trades, "passed": closed_trades >= min_trades},
        {"key": "track_record", "label": "Net PnL positive after gas",
         "passed": net_pnl_eth is not None and net_pnl_eth > 0},
        {"key": "model_fix", "label": "Model retrained on entry-time signals", "passed": bool(model_fix_done)},
        {"key": "cooldown", "label": f"{cooldown_days} days since last launch", "passed": cooldown_ok},
        {"key": "capital", "label": "Launch reserve funded", "current": balance_eth, "target": reserve_eth,
         "passed": balance_eth is not None and balance_eth >= reserve_eth},
    ]
    blocked_by = [c["key"] for c in conditions if not c["passed"]]
    if pending_launch:
        status = "forging"
    elif not blocked_by:
        status = "ready"
    elif blocked_by == ["cooldown"]:
        status = "cooldown"
    else:
        status = "locked"
    return {
        "status": status,
        "blocked_by": blocked_by,
        "conditions": conditions,
        "next_launch_possible_at": None if cooldown_ok or cooldown_until is None else cooldown_until.isoformat(),
    }


def net_pnl_after_gas(realized_eth: Optional[float], closed_trades: int, gas_eth_per_tx: float) -> Optional[float]:
    """Realized PnL minus an estimate of gas (a buy and a sell per closed trade). None when PnL is unknown."""
    if realized_eth is None:
        return None
    return realized_eth - 2 * closed_trades * gas_eth_per_tx


# ---------------------------------------------------------------------------
# Share text (X)
# ---------------------------------------------------------------------------

def strip_cashtag(text: Optional[str]) -> str:
    """X turns $TICKER into a stock card, usually for the wrong asset. Tickers are written without the `$`."""
    return re.sub(r"\$(?=\w)", "", text or "").strip()


# No "$" anywhere in share text: even "$30K" next to a ticker invites a stock card
VERDICT_LABEL = {"pending": "forging", "reached_30k": "reached the 30K market cap", "stalled": "stalled"}


def share_text(name: Optional[str], symbol: Optional[str], verdict: str, url: str) -> str:
    label = VERDICT_LABEL.get(verdict, verdict)
    head = strip_cashtag(name) or strip_cashtag(symbol) or "A Golem launch"
    sym = strip_cashtag(symbol)
    who = f"{head} ({sym})" if sym and sym.lower() != head.lower() else head
    return f"GoForge: {who} {label}. Every launch explained, win or lose. {url}"


# ---------------------------------------------------------------------------
# Serialization (REST and WS share it, so nothing leaks through the stream)
# ---------------------------------------------------------------------------

def is_public(row: dict) -> bool:
    """Visible only once the pool is active. Before that not even the name or the CA may leave the backend."""
    return row.get("pool_active_at") is not None


def _num(v) -> Optional[float]:
    return None if v is None else float(v)


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() if v else None


def serialize_launch(row: dict, latest: Optional[dict], entry: LaunchEntry, now: datetime, *, blockscout: str,
                     dex_chain: str, hours: int, stale_after_s: int, settled_stale_after_s: int,
                     public_url: str) -> Optional[dict]:
    """
    One launch card, or None while the launch is not public. `row` is a goforge_launches row, `latest` its newest
    snapshot (None when no market read ever succeeded: price, MC, liquidity, volume and holders stay None).
    """
    if not is_public(row):
        return None
    latest = latest or {}
    launched_at: Optional[datetime] = row.get("launched_at")
    verdict = row.get("verdict") or "pending"
    ok_at: Optional[datetime] = row.get("last_source_ok_at")
    limit = stale_after_s if verdict == "pending" else settled_stale_after_s
    age = None if ok_at is None else max(0, int((now - ok_at).total_seconds()))
    stale = age is None or age > limit
    ca = row["ca"]
    has_router = entry.fee_router_address is not None
    hours_since = None if launched_at is None else round(max(0.0, (now - launched_at).total_seconds()) / 3600, 2)
    return {
        "id": row["id"],
        "ca": ca,
        "name": row.get("name"),
        "symbol": strip_cashtag(row.get("symbol")) or None,
        "launched_at": _iso(launched_at),
        "hours_since_launch": hours_since,
        "seconds_to_verdict": seconds_to_verdict(verdict, launched_at, now, hours),
        "price_usd": _num(latest.get("price_usd")),
        "mc_usd": _num(latest.get("mc_usd")),
        # 0 is only the column default: with no market read at all the peak is unknown, not $0
        "peak_mc_usd": _num(row.get("peak_mc_usd")) if latest or row.get("peak_mc_usd") else None,
        "liquidity_usd": _num(latest.get("liquidity_usd")),
        "volume_24h_usd": _num(latest.get("volume_24h_usd")),
        "holders": latest.get("holders"),
        "top10_holder_pct": _num(row.get("top10_pct")),
        "verdict": verdict,
        "verdict_at": _iso(row.get("verdict_at")),
        # Unknown until a FeeRouter exists: None, not 0
        "fees_usd": _num(row.get("fees_usd")) if has_router else None,
        "epc_burned": _num(row.get("epc_burned")) if has_router else None,
        "stale": stale,
        "stale_age_s": age,
        "market_updated_at": _iso(ok_at),
        "links": {
            "blockscout_token": f"{blockscout}/token/{ca}",
            "launch_tx": f"{blockscout}/tx/{row['launch_tx']}",
            "dexscreener": row.get("pair_url") or f"https://dexscreener.com/{dex_chain}/{ca}",
        },
        "why": {k: entry.why.get(k) for k in WHY_KEYS},
        "rules": dict(entry.rules),
        "rules_pending": all(v is None for v in entry.rules.values()),
        "fee_router_address": entry.fee_router_address,
        "share_text": share_text(row.get("name"), row.get("symbol"), verdict, f"{public_url}"),
    }


def totals(launches: Iterable[dict]) -> dict:
    """Counters over the public launches. Fees and burn stay None until some launch has a FeeRouter."""
    rows = list(launches)
    with_fees = [r for r in rows if r["fees_usd"] is not None]
    with_burn = [r for r in rows if r["epc_burned"] is not None]
    return {
        "launches": len(rows),
        "reached_30k": sum(1 for r in rows if r["verdict"] == "reached_30k"),
        "stalled": sum(1 for r in rows if r["verdict"] == "stalled"),
        "pending": sum(1 for r in rows if r["verdict"] == "pending"),
        "fees_usd": sum(r["fees_usd"] for r in with_fees) if with_fees else None,
        "epc_burned": sum(r["epc_burned"] for r in with_burn) if with_burn else None,
    }


def serialize_history(snapshots: list[dict], launched_at: Optional[datetime], *, hours: int,
                      target_usd: float) -> dict:
    """MC and price points of the first `hours` hours, oldest first. A snapshot with no MC is not drawn."""
    points = []
    if launched_at is not None:
        for s in sorted(snapshots, key=lambda s: s["ts"]):
            if s.get("mc_usd") is None or not in_window(s["ts"], launched_at, hours):
                continue
            points.append({"ts": s["ts"].isoformat(), "mc_usd": float(s["mc_usd"]),
                           "price_usd": _num(s.get("price_usd"))})
    return {"target_mc_usd": target_usd, "window_hours": hours, "points": points}


def holders_concentration(holders: list[dict], total_supply: Optional[float]) -> Optional[float]:
    """Share of the supply (percent) held by the top 10 holders, from Blockscout's holders list."""
    if not holders or not total_supply:
        return None
    top = sorted((float(h["value"]) for h in holders if h.get("value") is not None), reverse=True)[:10]
    if not top:
        return None
    return round(sum(top) / float(total_supply) * 100, 2)
