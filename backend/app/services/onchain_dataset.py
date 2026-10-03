"""
Clean dataset for demo trading. Pure functions over a pair's Sync history; the builder script feeds them chain data.

Entry is the first Sync at or above $10K market cap. From that block on, inside a fixed horizon:
- take profit: first Sync at or above $30K
- stop loss:   first Sync at or below $5K
Sync events are ordered by (block, logIndex), so whichever barrier comes first is exact, never ambiguous.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MC_ENTRY = 10_000.0
MC_TP = 30_000.0
MC_SL = 5_000.0
HORIZON = timedelta(hours=48)
ZERO = "0x" + "0" * 40


@dataclass(frozen=True)
class SyncPoint:
    block: int
    log_index: int
    at: datetime
    mc_usd: float
    liq_usd: float  # both sides of the pool: 2 x WETH reserve x ETH/USD


def first_entry(points: list[SyncPoint]) -> Optional[int]:
    """Index of the first Sync at or above $10K (first crossing only; later re-crossings never count)."""
    for i, p in enumerate(points):
        if p.mc_usd >= MC_ENTRY:
            return i
    return None


def barrier_outcome(points: list[SyncPoint], entry: int, now: datetime) -> dict:
    """
    Triple-barrier outcome from points[entry]. `label_30k_48h` is True/False only once it is decided:
    True as soon as TP is touched, False once the horizon has fully passed without TP. NULL (None) otherwise.
    The SL time is recorded but does not decide the label: the label answers "did it reach $30K",
    and the SL-free answer is the shadow metric the demo compares against.
    """
    e = points[entry]
    end = e.at + HORIZON
    window = [p for p in points[entry:] if p.at <= end]
    tp = next((p for p in window if p.mc_usd >= MC_TP), None)
    sl = next((p for p in window if p.mc_usd <= MC_SL), None)
    closed = now >= end
    if tp is not None:
        label = True
    elif closed:
        label = False
    else:
        label = None
    first = None
    if tp and (not sl or (tp.block, tp.log_index) < (sl.block, sl.log_index)):
        first = "tp"
    elif sl:
        first = "sl"
    elif closed:
        first = "timeout"
    return {
        "horizon_end_at": end,
        "window_closed": closed,
        "tp_block": tp.block if tp else None,
        "tp_at": tp.at if tp else None,
        "sl_block": sl.block if sl else None,
        "sl_at": sl.at if sl else None,
        "first_barrier": first,
        "peak_mc_h": max(p.mc_usd for p in window),
        "min_mc_h": min(p.mc_usd for p in window),
        "last_mc_h": window[-1].mc_usd,
        "min_liq_h": min(p.liq_usd for p in window),
        "label_30k_48h": label,
    }


def holders_at(transfers: list[dict], exclude: set[str]) -> int:
    """Addresses with a non-zero balance after `transfers` (decoded, already cut at the entry block)."""
    bal: dict[str, int] = {}
    for t in transfers:
        bal[t["from"]] = bal.get(t["from"], 0) - t["amount_wei"]
        bal[t["to"]] = bal.get(t["to"], 0) + t["amount_wei"]
    return sum(1 for a, v in bal.items() if v > 0 and a not in exclude)
