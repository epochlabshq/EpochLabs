"""
The Desk domain logic. Pure functions only (no I/O), so state, anonymity, PnL and the Why card are unit-testable.

Rules (Developer Brief: The Desk):
- Every number comes from the chain or Golem's decision log. Trades and PnL are rebuilt from golem_swaps
  (decoded receipts), never from internal bookkeeping.
- A candidate's identity never leaves the backend before its entry tx confirms. Waiting slots are built from
  a whitelist of fields; Watching rows never read the candidate table.
- `why` is written once at decision time as canonical JSON; its sha256 lets anyone check it was not rewritten.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

WEI = 10 ** 18

STATES = ("gated", "watching", "waiting", "entering", "in_position", "paused")
WAITING_STAGES = ("liquidity_check", "sizing", "entering")
EXIT_REASONS = ("take_profit", "stop_loss", "max_hold")
# A position counts as closed once what is left is below this share of what was bought (rounding dust)
DUST_FRACTION = 1e-3


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def gated_reason(model: Optional[dict], target_auc: float) -> str:
    """What still blocks Epoch II: the model's first failing gate, else the floor, else the watcher's record."""
    if model is None:
        return "no_model_run"
    if model["blocked_by"]:
        return model["blocked_by"]
    if model["proven_floor"] < target_auc:
        return "proven_floor"
    return "epoch_ii_pending"


def derive_state(epoch2_complete: bool, paused_reason: Optional[str], n_open: int,
                 waiting_stages: Iterable[str]) -> str:
    """Gated before Epoch II, Paused when a gate fails after unlock, otherwise what Golem is busy with."""
    if not epoch2_complete:
        return "gated"
    if paused_reason:
        return "paused"
    if n_open:
        return "in_position"
    stages = set(waiting_stages)
    if "entering" in stages:
        return "entering"
    if stages:
        return "waiting"
    return "watching"


def heartbeat(last_decision_at: Optional[datetime], now: datetime, warn_after_s: int) -> dict:
    if last_decision_at is None:
        return {"last_decision_at": None, "age_s": None, "stale": True, "warn_after_s": warn_after_s}
    age = max(0, int((now - last_decision_at).total_seconds()))
    return {"last_decision_at": last_decision_at.isoformat(), "age_s": age, "stale": age > warn_after_s,
            "warn_after_s": warn_after_s}


# ---------------------------------------------------------------------------
# Watching and Waiting (front-running protection)
# ---------------------------------------------------------------------------

def watching_rows(tokens: list[dict], threshold: float, excluded: frozenset[str], limit: int,
                  take_profit_mc_usd: float = 30_000) -> list[dict]:
    """
    tokens: rows of tokens joined with desk_scores (mint, name, symbol, peak_mc, launched_at, holders, survival).
    Built only from public feed data and the score: nothing here may depend on the candidate queue.
    """
    rows = []
    for t in tokens:
        survival = t.get("survival")
        mc_now = t.get("mc_now")
        if t["mint"].lower() in excluded:
            status = "excluded"
        elif mc_now is not None and mc_now >= take_profit_mc_usd:
            # Already at the take-profit: the outcome is known, so it is neither scored nor bought
            status = "reached_tp"
        elif survival is None:
            # No holder count yet: the model cannot score it honestly (see app.services.scorer)
            status = "awaiting_holders" if t.get("holders") is None else "unscored"
        else:
            status = "scoring" if survival >= threshold else "below_threshold"
        rows.append({
            "token": {"name": t["name"], "symbol": t["symbol"], "address": t["mint"],
                      "dexscreener_url": t.get("pair_url") or f"https://dexscreener.com/robinhood/{t['mint'].lower()}"},
            "mc_now": float(mc_now) if mc_now is not None else None,
            "mc_at": t["mc_at"].isoformat() if t.get("mc_at") else None,
            "peak_mc": float(t["peak_mc"]) if t.get("peak_mc") is not None else None,
            "launched_at": t["launched_at"].isoformat() if t.get("launched_at") else None,
            "holders": t.get("holders"),
            "holders_sampled_at": t["holders_sampled_at"].isoformat() if t.get("holders_sampled_at") else None,
            "survival": round(survival, 4) if survival is not None and status != "reached_tp" else None,
            "status": status,
        })
    rows.sort(key=lambda r: (r["survival"] is None, -(r["survival"] or 0)))
    return rows[:limit]


def anonymize_waiting(candidates: list[dict], now: datetime, visible_dropped_h: int) -> list[dict]:
    """
    Waiting slots from desk_candidates rows. Whitelist only: slot number, stage, timing, and a drop reason.
    The exact survival is withheld on purpose: Watching publishes every token's exact score, so an exact
    score on a slot would identify the candidate. A slot only says it cleared the entry threshold.
    """
    out = []
    for c in sorted(candidates, key=lambda c: c["queued_at"]):
        if c["stage"] in WAITING_STAGES:
            out.append({"slot": c["id"], "stage": c["stage"], "queued_at": c["queued_at"].isoformat()})
        elif c["stage"] == "dropped" and c["dropped_at"] >= now - timedelta(hours=visible_dropped_h):
            out.append({
                "slot": c["id"], "stage": "dropped", "queued_at": c["queued_at"].isoformat(),
                "dropped_reason": c["dropped_reason"], "dropped_at": c["dropped_at"].isoformat(),
            })
    return out


def revealed_drops(candidates: list[dict], now: datetime, reveal_h: int, limit: int = 20) -> list[dict]:
    """Dropped candidates whose identity may be shown: only after the reveal delay has passed."""
    cutoff = now - timedelta(hours=reveal_h)
    rows = [c for c in candidates if c["stage"] == "dropped" and c["dropped_at"] <= cutoff]
    rows.sort(key=lambda c: c["dropped_at"], reverse=True)
    return [{
        "slot": c["id"], "token": c["token"], "survival": round(c["survival"], 4),
        "dropped_reason": c["dropped_reason"], "dropped_at": c["dropped_at"].isoformat(),
    } for c in rows[:limit]]


# ---------------------------------------------------------------------------
# Why card
# ---------------------------------------------------------------------------

def canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def why_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_why(*, survival: float, threshold: float, top_signals: list[dict], model_run_id: int,
              proven_floor: float, size_eth: float, size_rule: str, take_profit_mc_usd: float,
              stop_loss_mc_usd: float, max_hold_h: int, decided_at: datetime) -> dict:
    if not 0.0 <= survival <= 1.0:
        raise ValueError("survival must be within [0, 1]")
    if survival < threshold:
        raise ValueError("an entry below the threshold has no valid Why")
    if not top_signals:
        raise ValueError("top_signals is required")
    return {
        "survival": round(survival, 4),
        "threshold": threshold,
        "top_signals": [
            {"name": s["name"], "value": s["value"], "effect": s["effect"]} for s in top_signals[:3]
        ],
        "model_run_id": model_run_id,
        "proven_floor": round(proven_floor, 4),
        "size_eth": size_eth,
        "size_rule": size_rule,
        "exit_plan": {
            "take_profit_mc_usd": take_profit_mc_usd,
            "stop_loss_mc_usd": stop_loss_mc_usd,
            "max_hold_h": max_hold_h,
        },
        "decided_at": decided_at.isoformat(),
    }


def verify_why(canonical: Optional[str], sha256: Optional[str]) -> bool:
    return bool(canonical) and bool(sha256) and why_hash(canonical) == sha256


# ---------------------------------------------------------------------------
# Trades from onchain swaps
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    tx_hash: str
    block: int
    at: datetime
    token_wei: int
    eth_wei: int


@dataclass
class Trade:
    token: str
    buys: list[Fill] = field(default_factory=list)
    sells: list[Fill] = field(default_factory=list)
    id: Optional[str] = None
    incomplete: bool = False  # a swap on this token could not be valued in ETH

    @property
    def bought_wei(self) -> int:
        return sum(f.token_wei for f in self.buys)

    @property
    def sold_wei(self) -> int:
        return sum(f.token_wei for f in self.sells)

    @property
    def held_wei(self) -> int:
        return max(0, self.bought_wei - self.sold_wei)

    @property
    def cost_wei(self) -> int:
        return sum(f.eth_wei for f in self.buys)

    @property
    def proceeds_wei(self) -> int:
        return sum(f.eth_wei for f in self.sells)

    @property
    def closed(self) -> bool:
        return bool(self.sells) and self.held_wei <= self.bought_wei * DUST_FRACTION

    @property
    def entry(self) -> Fill:
        return self.buys[0]

    @property
    def exit(self) -> Optional[Fill]:
        return self.sells[-1] if self.closed else None


def group_trades(swaps: list[dict]) -> list[Trade]:
    """
    swaps: golem_swaps rows (tx_hash, block, at, side, token, token_amount_wei, eth_amount_wei).
    A trade opens on a buy while nothing of that token is held and closes once it is sold down to dust.
    Sells of tokens Golem never bought (no open trade) are ignored. Ids follow entry order and never change,
    because the swap history is append-only.
    """
    open_by_token: dict[str, Trade] = {}
    trades: list[Trade] = []
    for s in sorted(swaps, key=lambda r: (r["block"], r["tx_hash"])):
        token = (s.get("token") or "").lower()
        if not token or s.get("side") not in ("buy", "sell"):
            if token in open_by_token:
                open_by_token[token].incomplete = True
            continue
        if s.get("token_amount_wei") is None or s.get("eth_amount_wei") is None:
            if token in open_by_token:
                open_by_token[token].incomplete = True
            continue
        fill = Fill(s["tx_hash"], s["block"], s["at"], int(s["token_amount_wei"]), int(s["eth_amount_wei"]))
        trade = open_by_token.get(token)
        if s["side"] == "buy":
            if trade is None:
                trade = Trade(token)
                trades.append(trade)
                open_by_token[token] = trade
            trade.buys.append(fill)
        else:
            if trade is None:
                continue
            trade.sells.append(fill)
            if trade.closed:
                del open_by_token[token]
    for i, t in enumerate(trades):
        t.id = f"t_{i + 1:04d}"
    return trades


def _eth(wei: Optional[int]) -> Optional[float]:
    return None if wei is None else wei / WEI


def _pct(pnl_wei: Optional[int], cost_wei: int) -> Optional[float]:
    if pnl_wei is None or not cost_wei:
        return None
    return round(pnl_wei / cost_wei * 100, 4)


def realized_pnl_wei(t: Trade) -> int:
    """Proceeds minus the cost of the tokens sold (average cost within this trade)."""
    if not t.bought_wei:
        return 0
    return t.proceeds_wei - t.cost_wei * min(t.sold_wei, t.bought_wei) // t.bought_wei


def unrealized_pnl_wei(t: Trade, mark: Optional[tuple[int, int]]) -> Optional[int]:
    """mark = (weth_reserve, token_reserve) of the token's WETH pair. Held tokens valued at the mid price."""
    if t.closed:
        return 0
    if not mark or not mark[1]:
        return None
    held_cost = t.cost_wei - (t.cost_wei * min(t.sold_wei, t.bought_wei) // t.bought_wei if t.bought_wei else 0)
    return t.held_wei * mark[0] // mark[1] - held_cost


def price_eth(eth_wei: int, token_wei: int, decimals: Optional[int]) -> Optional[float]:
    """ETH per whole token; None while the token's decimals are unknown."""
    if decimals is None or not token_wei:
        return None
    return eth_wei / WEI / (token_wei / 10 ** decimals)


@dataclass(frozen=True)
class Decision:
    """A golem_trade_decisions row linked to a confirmed swap."""
    id: int
    side: str
    why_canonical: Optional[str]
    why_sha256: Optional[str]
    exit_reason: Optional[str]


def _token_block(address: str, meta: dict, blockscout: str) -> dict:
    m = meta.get(address) or {}
    return {"name": m.get("name"), "symbol": m.get("symbol"), "address": address,
            "url": f"{blockscout}/address/{address}"}


def _why_block(d: Optional[Decision]) -> dict:
    if d is None:
        return {"why": None, "why_sha256": None, "why_verified": False}
    return {
        "why": json.loads(d.why_canonical) if d.why_canonical else None,
        "why_sha256": d.why_sha256,
        "why_verified": verify_why(d.why_canonical, d.why_sha256),
    }


def serialize_open(t: Trade, *, meta: dict, decisions: dict[str, Decision], mark: Optional[tuple[int, int]],
                   decimals: Optional[int], blockscout: str) -> dict:
    e = t.entry
    upnl = unrealized_pnl_wei(t, mark)
    rpnl = realized_pnl_wei(t)
    pnl = None if upnl is None else upnl + rpnl
    mark_price = None
    if mark and mark[1] and decimals is not None:
        mark_price = price_eth(mark[0], mark[1], decimals)
    return {
        "id": t.id,
        "token": _token_block(t.token, meta, blockscout),
        "entry": {
            "at": e.at.isoformat(), "price": price_eth(t.cost_wei, t.bought_wei, decimals),
            "size_eth": _eth(t.cost_wei), "tx": e.tx_hash, "tx_url": f"{blockscout}/tx/{e.tx_hash}",
        },
        "mark_price": mark_price,
        "pnl": {"eth": _eth(pnl), "pct": _pct(pnl, t.cost_wei)},
        "incomplete": t.incomplete,
        **_why_block(decisions.get(e.tx_hash)),
    }


def serialize_closed(t: Trade, *, meta: dict, decisions: dict[str, Decision], decimals: Optional[int],
                     blockscout: str, include_why: bool = False) -> dict:
    e, x = t.entry, t.exit
    pnl = realized_pnl_wei(t)
    exit_decision = decisions.get(x.tx_hash)
    why = _why_block(decisions.get(e.tx_hash))
    out = {
        "id": t.id,
        "token": _token_block(t.token, meta, blockscout),
        "entry": {
            "at": e.at.isoformat(), "price": price_eth(t.cost_wei, t.bought_wei, decimals),
            "size_eth": _eth(t.cost_wei), "tx": e.tx_hash, "tx_url": f"{blockscout}/tx/{e.tx_hash}",
        },
        "exit": {
            "at": x.at.isoformat(), "price": price_eth(t.proceeds_wei, t.sold_wei, decimals),
            "proceeds_eth": _eth(t.proceeds_wei), "tx": x.tx_hash, "tx_url": f"{blockscout}/tx/{x.tx_hash}",
        },
        "duration_s": int((x.at - e.at).total_seconds()),
        "pnl": {"eth": _eth(pnl), "pct": _pct(pnl, t.cost_wei)},
        "exit_reason": exit_decision.exit_reason if exit_decision else None,
        # Did the token reach $30K in the end? From its 48h label; None while still unlabeled
        "reached_30k": {"passed": True, "stalled": False}.get((meta.get(t.token) or {}).get("status")),
        "epc_burned": None,  # set once the burn rule (per trade or per period) is decided
        "incomplete": t.incomplete,
        "why_sha256": why["why_sha256"],
        "why_verified": why["why_verified"],
    }
    if include_why:
        out["why"] = why["why"]
    return out


def totals(open_rows: list[dict], closed_rows: list[dict], start_eth: float) -> dict:
    """Net PnL since the first trade: realized on closed trades plus live PnL on open ones."""
    realized = sum(r["pnl"]["eth"] for r in closed_rows if r["pnl"]["eth"] is not None)
    live = [r["pnl"]["eth"] for r in open_rows]
    complete = all(v is not None for v in live)
    eth = realized + sum(v for v in live if v is not None)
    return {
        "eth": round(eth, 12),
        "pct": round(eth / start_eth * 100, 4) if start_eth else None,
        "realized_eth": round(realized, 12),
        "complete": complete,  # False while an open position has no mark price
        "wins": sum(1 for r in closed_rows if (r["pnl"]["eth"] or 0) > 0),
        "losses": sum(1 for r in closed_rows if (r["pnl"]["eth"] or 0) < 0),
    }
