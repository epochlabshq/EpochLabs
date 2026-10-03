"""
X auto-post for The Desk. Each desk_announcements row (one per trade open/close) is posted once, after
DESK_X_POST_DELAY_SECONDS, with up to DESK_X_POST_MAX_ATTEMPTS tries; a failure is logged and never blocks
trading. Post text is rendered from the same trade detail (and Why card) that /api/desk/trades/{id} serves.

No "$" before EPC or any ticker: X turns "$EPC" into a stock card for Edgewell Personal Care.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from app.core.config import settings

X_MAX_WEIGHTED = 280
X_URL_WEIGHT = 23
_URL = re.compile(r"https?://\S+")
_CASHTAG = re.compile(r"\$(?=[A-Za-z])")

DISCLAIMER = "Research experiment. Not financial advice."
EXIT_REASON_TEXT = {"take_profit": "take-profit", "stop_loss": "stop-loss", "max_hold": "time limit"}


def strip_cashtags(s: str) -> str:
    """'$EPC' -> 'EPC', '$MDOG' -> 'MDOG'. Dollar amounts like '$30K' are left alone."""
    return _CASHTAG.sub("", s)


def x_length(s: str) -> int:
    """Length as X counts it for our text: every URL counts as 23, other characters as 1 (posts are ASCII)."""
    return len(_URL.sub("x" * X_URL_WEIGHT, s))


def _ticker(token: dict) -> str:
    t = token.get("symbol") or token.get("name") or f"{token['address'][:6]}…{token['address'][-4:]}"
    return strip_cashtags(t)


def _signed(v: Optional[float], digits: int) -> str:
    if v is None:
        return "n/a"
    s = f"{abs(v):.{digits}f}"
    return s if float(s) == 0 else f"{'-' if v < 0 else '+'}{s}"


def _duration(seconds: int) -> str:
    h, m = divmod(max(0, seconds) // 60, 60)
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def _fit(lines: list[str], optional: list[int]) -> str:
    """Join lines; if too long for X, drop optional lines (by index, in the given order) until it fits."""
    keep = list(lines)
    for i in optional:
        text_ = strip_cashtags("\n".join(l for l in keep if l is not None))
        if x_length(text_) <= X_MAX_WEIGHTED:
            return text_
        keep[i] = None
    text_ = strip_cashtags("\n".join(l for l in keep if l is not None))
    if x_length(text_) > X_MAX_WEIGHTED:
        raise ValueError("post does not fit on X even after trimming")
    return text_


def _signal_line(s: dict, max_value: int = 28) -> str:
    value = s["value"] if len(s["value"]) <= max_value else s["value"][: max_value - 1] + "…"
    return f"{'+' if s['effect'] == '+' else '-' if s['effect'] == '-' else '·'} {s['name'].replace('_', ' ')} {value}"


def render_entry_post(trade: dict, desk_url: str) -> str:
    """Entry post: token, size, survival vs threshold, top three signals, tx link, /desk link, disclaimer."""
    why = trade.get("why")
    if not why:
        raise ValueError("entry post needs the trade's Why card")
    signals = [_signal_line(s) for s in why["top_signals"][:3]]
    lines = [
        f"Golem entered {_ticker(trade['token'])}",
        f"Size {why['size_eth']:.2f} ETH · survival {why['survival']:.2f} (threshold {why['threshold']:.2f})",
        *signals,
        f"tx {trade['entry']['tx_url']}",
        f"Why: {desk_url}",
        DISCLAIMER,
    ]
    # Trim the weakest signals first if the post runs long
    return _fit(lines, optional=[1 + len(signals), 1 + len(signals) - 1, 1 + len(signals) - 2][: len(signals)])


def render_exit_post(trade: dict, desk_url: str) -> str:
    """Exit post: token, PnL, duration, exit reason, EPC burned, tx link."""
    pnl = trade["pnl"]
    reason = EXIT_REASON_TEXT.get(trade.get("exit_reason") or "", "exit rule not logged")
    burned = trade.get("epc_burned")
    lines = [
        f"Golem closed {_ticker(trade['token'])}: {_signed(pnl['eth'], 4)} ETH ({_signed(pnl['pct'], 1)}%)",
        f"Held {_duration(trade['duration_s'])} · {reason}",
        f"EPC burned: {burned:,.0f}" if burned is not None else "EPC burned: pending burn rule",
        f"tx {trade['exit']['tx_url']}",
        desk_url,
        DISCLAIMER,
    ]
    return _fit(lines, optional=[])


def render_post(kind: str, trade: dict, desk_url: str) -> str:
    return render_entry_post(trade, desk_url) if kind == "open" else render_exit_post(trade, desk_url)


def initial_post_status(event_at: datetime, now: datetime, max_age_h: int) -> str:
    """History found on first deploy is announced over WS but never posted to X."""
    return "skipped" if event_at < now - timedelta(hours=max_age_h) else "pending"


def next_post_status(send_status: str, attempts: int, max_attempts: int) -> str:
    """After one send: done, retried on the next cycle, or given up (logged; trading is never affected)."""
    if send_status == "sent":
        return "posted"
    if send_status == "dry_run":
        return "dry_run"
    return "failed" if attempts >= max_attempts else "pending"


# ---- Queue ------------------------------------------------------------------------------------------------

async def post_due(db, inp, poster) -> int:
    """
    Post every pending announcement whose delay has passed. `inp` is the DeskInputs of this cycle and
    `poster` has `post_desk_tweet(text, trigger_type)`. Returns how many rows were attempted.
    """
    from app.api.desk_endpoints import find_trade

    now = datetime.now(timezone.utc)
    rows = (await db.execute(text(
        "SELECT trade_id, kind, post_attempts FROM desk_announcements "
        "WHERE post_status = 'pending' AND announced_at <= :due ORDER BY announced_at"
    ), {"due": now - timedelta(seconds=settings.DESK_X_POST_DELAY_SECONDS)})).mappings().all()
    for r in rows:
        trade = find_trade(inp, r["trade_id"])
        try:
            if trade is None:
                raise ValueError("trade not found")
            body = render_post(r["kind"], trade, settings.DESK_PUBLIC_URL)
        except ValueError as e:
            # Nothing honest to post (e.g. a trade with no logged Why): record it and move on
            await db.execute(text(
                "UPDATE desk_announcements SET post_status = 'skipped', post_error = :e "
                "WHERE trade_id = :id AND kind = :k"
            ), {"e": str(e), "id": r["trade_id"], "k": r["kind"]})
            await db.commit()
            print(f"[DESK POSTER] {r['trade_id']} {r['kind']} skipped: {e}", flush=True)
            continue

        res = await poster.post_desk_tweet(body, f"desk_{'entry' if r['kind'] == 'open' else 'exit'}")
        attempts = r["post_attempts"] + 1
        status = next_post_status(res["status"], attempts, settings.DESK_X_POST_MAX_ATTEMPTS)
        await db.execute(text(
            "UPDATE desk_announcements SET post_status = :s, post_attempts = :a, tweet_id = :t, post_error = :e, "
            "posted_at = CASE WHEN :s IN ('posted', 'dry_run') THEN now() ELSE posted_at END "
            "WHERE trade_id = :id AND kind = :k"
        ), {"s": status, "a": attempts, "t": res.get("tweet_id"), "e": res.get("error_message"),
            "id": r["trade_id"], "k": r["kind"]})
        await db.commit()
        if status == "failed":
            print(f"[DESK POSTER] {r['trade_id']} {r['kind']} failed after {attempts} attempts: "
                  f"{res.get('error_message')}", flush=True)
    return len(rows)
