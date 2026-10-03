"""
Golem executor: turns live scores into entries and exit rules into sells. Runs inside the Desk worker cycle.

Modes (DESK_EXECUTOR_MODE):
- off      nothing happens (default).
- dry_run  every check runs against live chain data and the plan is logged; nothing is written, nothing is sent.
- live     candidates, decisions and swaps for real. Needs a signer registered with `register_signer`; without
           one the executor refuses to act. Which key signs (Golem wallet or agent) is still an Open question.

Live pipeline, one stage per cycle so each candidate is visible in Waiting before Golem enters:
  queue (liquidity_check) -> sizing -> entering (decision logged with its Why, swap sent) -> open (indexer)
Exits run every cycle, also while Golem is paused, and never wait for the gate.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from sqlalchemy import text

from app.core.config import settings
from app.services import desk_chain
from app.services.desk import build_why, group_trades
from app.services.desk_trading import (
    TxRequest, build_buy_tx, build_sell_tx, encode_approve, exit_reason, liquidity_check, mc_usd, select_candidates,
    size_position,
)
from app.services.golem_guard import GolemPausedError, attach_trade_tx, current_trade_gate, record_trade_decision

SEL_ALLOWANCE = "0xdd62ed3e"  # allowance(address,address)
MAX_UINT = 2 ** 256 - 1


class Signer(Protocol):
    """Signs and broadcasts from the Golem wallet. Returns the tx hash. Must manage its own nonces."""
    address: str

    async def send(self, tx: TxRequest) -> str: ...


_signer: Optional[Signer] = None


def register_signer(signer: Optional[Signer]) -> None:
    global _signer
    if signer is not None and signer.address.lower() != settings.GOLEM_WALLET.lower():
        raise ValueError("signer must send from the published Golem wallet")
    _signer = signer


def _trading_params() -> dict:
    return dict(min_liq_usd=settings.DESK_MIN_LIQ_USD, stop_loss_mc_usd=settings.DESK_SL_MC_USD,
                take_profit_mc_usd=settings.DESK_TP_MC_USD)


async def _market(r, tokens: list[str]) -> tuple[dict, dict, Optional[float]]:
    marks = await desk_chain.marks(r, tokens)
    supplies = await desk_chain.total_supplies(r, tokens)
    return marks, supplies, await desk_chain.eth_usd()


async def _scores(db, run_id: int) -> dict[str, dict]:
    rows = (await db.execute(text(
        "SELECT lower(mint) AS mint, survival, top_signals FROM desk_scores WHERE run_id = :r"
    ), {"r": run_id})).mappings().all()
    # Raw text() queries may hand JSONB back as a string depending on the driver codec
    return {r["mint"]: {**r, "top_signals": json.loads(r["top_signals"]) if isinstance(r["top_signals"], str)
                        else r["top_signals"]} for r in rows}


async def _busy_tokens(db, open_tokens: list[str]) -> set[str]:
    """Held, queued, or dropped within the reveal window (a dropped token is not retried until it is public)."""
    rows = (await db.execute(text(
        "SELECT lower(token) FROM desk_candidates WHERE stage IN ('liquidity_check', 'sizing', 'entering') "
        "OR (stage = 'dropped' AND dropped_at > now() - make_interval(hours => :h))"
    ), {"h": settings.DESK_DROPPED_REVEAL_H})).all()
    return {r[0] for r in rows} | {t.lower() for t in open_tokens}


async def _drop(db, cand_id: int, reason: str) -> None:
    await db.execute(text(
        "UPDATE desk_candidates SET stage = 'dropped', dropped_reason = :r, dropped_at = now() WHERE id = :id"
    ), {"r": reason, "id": cand_id})
    await db.commit()
    # Slot number only: the token stays private until the reveal window passes
    print(f"[DESK EXECUTOR] candidate #{cand_id} dropped: {reason}", flush=True)


def _deadline() -> int:
    return int(time.time()) + settings.DESK_TX_DEADLINE_SECONDS


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

async def _pending_sell_tokens(db) -> set[str]:
    """Tokens with a sell decision whose swap is not indexed yet: never send a second sell meanwhile."""
    rows = (await db.execute(text(
        "SELECT lower(d.token) FROM golem_trade_decisions d LEFT JOIN golem_swaps s ON s.tx_hash = d.tx_hash "
        "WHERE d.side = 'sell' AND s.tx_hash IS NULL AND d.decided_at > now() - make_interval(mins => :m)"
    ), {"m": settings.DESK_ENTERING_TIMEOUT_MINUTES})).all()
    return {r[0] for r in rows}


async def run_exits(db, r, inp, scores: dict, live: bool) -> list[str]:
    now = datetime.now(timezone.utc)
    open_trades = [t for t in group_trades(inp.swaps) if not t.closed]
    if not open_trades:
        return []
    tokens = [t.token for t in open_trades]
    marks, supplies, eth_usd = await _market(r, tokens)
    pending = await _pending_sell_tokens(db) if live else set()
    log = []
    for t in open_trades:
        mark = marks.get(t.token)
        mc = mc_usd(mark[0], mark[1], supplies[t.token], eth_usd) if mark and t.token in supplies and eth_usd else None
        reason = exit_reason(mc_now=mc, entered_at=t.entry.at, now=now, take_profit_mc_usd=settings.DESK_TP_MC_USD,
                             stop_loss_mc_usd=settings.DESK_SL_MC_USD, max_hold_h=settings.DESK_MAX_HOLD_H)
        if reason is None:
            continue
        if not live:
            log.append(f"would sell {t.id} ({t.token}): {reason}, mc={mc}")
            continue
        if t.token in pending or not mark:
            continue
        survival = (scores.get(t.token) or {}).get("survival")
        if survival is None:
            survival = _entry_survival(inp, t) or 0.0
        decision_id = await record_trade_decision(
            db, token=t.token, side="sell", survival_probability=survival, top_signal=f"exit:{reason}",
            exit_reason=reason)
        amount = t.held_wei
        allowance = await _allowance(r, t.token)
        if allowance is not None and allowance < amount:
            await _signer.send(TxRequest(t.token, encode_approve(settings.UNISWAP_V2_ROUTER, MAX_UINT), 0))
        tx = build_sell_tx(amount_wei=amount, weth_reserve=mark[0], token_reserve=mark[1], weth=settings.WETH,
                           token=t.token, wallet=settings.GOLEM_WALLET, router=settings.UNISWAP_V2_ROUTER,
                           slippage_bps=settings.DESK_SLIPPAGE_BPS, deadline=_deadline())
        tx_hash = await _signer.send(tx)
        await attach_trade_tx(db, decision_id, tx_hash)
        log.append(f"sell sent for {t.id}: {reason} tx={tx_hash}")
    return log


def _entry_survival(inp, t) -> Optional[float]:
    d = inp.decisions.get(t.entry.tx_hash)
    return json.loads(d.why_canonical)["survival"] if d and d.why_canonical else None


async def _allowance(r, token: str) -> Optional[int]:
    data = "0x" + SEL_ALLOWANCE[2:] + settings.GOLEM_WALLET.lower()[2:].rjust(64, "0") \
        + settings.UNISWAP_V2_ROUTER.lower()[2:].rjust(64, "0")
    res = await r.eth_calls([(token, data)])
    return int(res[0], 16) if res and res[0] and res[0] != "0x" else None


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

async def advance_candidates(db, r, inp, model: dict, scores: dict, gate_open: bool) -> list[str]:
    """Move each queued candidate one stage forward (live only)."""
    log = []
    cands = (await db.execute(text(
        "SELECT id, lower(token) AS token, queued_at, survival, stage, decision_id FROM desk_candidates "
        "WHERE stage IN ('liquidity_check', 'sizing', 'entering') ORDER BY queued_at"
    ))).mappings().all()
    if not cands:
        return log
    tokens = [c["token"] for c in cands]
    marks, supplies, eth_usd = await _market(r, tokens)
    now = datetime.now(timezone.utc)
    for c in cands:
        if c["stage"] == "entering":
            if now - c["queued_at"] > timedelta(minutes=settings.DESK_ENTERING_TIMEOUT_MINUTES):
                await _drop(db, c["id"], "entry not confirmed in time")
            continue
        if not gate_open:
            await _drop(db, c["id"], "Golem paused")
            continue
        score = scores.get(c["token"])
        if score is None or score["survival"] < settings.DESK_ENTRY_THRESHOLD:
            await _drop(db, c["id"], "survival fell below the threshold")
            continue
        mark = marks.get(c["token"])
        check = liquidity_check(weth_reserve=mark[0] if mark else None, token_reserve=mark[1] if mark else None,
                                total_supply=supplies.get(c["token"]), eth_usd=eth_usd, **_trading_params())
        if not check.ok:
            await _drop(db, c["id"], check.reason)
            continue
        if c["stage"] == "liquidity_check":
            await db.execute(text("UPDATE desk_candidates SET stage = 'sizing' WHERE id = :id"), {"id": c["id"]})
            await db.commit()
            log.append(f"candidate #{c['id']} passed the liquidity check")
            continue

        # sizing -> entering: log the decision with its Why, then send the buy
        sizing = size_position(inp.balance_wei, size_eth=settings.DESK_POSITION_SIZE_ETH,
                               max_fraction=settings.DESK_MAX_WALLET_FRACTION, gas_reserve_eth=settings.DESK_GAS_RESERVE_ETH)
        if sizing.size_wei is None:
            await _drop(db, c["id"], sizing.reason)
            continue
        why = build_why(
            survival=score["survival"], threshold=settings.DESK_ENTRY_THRESHOLD, top_signals=score["top_signals"],
            model_run_id=model["run_id"], proven_floor=model["proven_floor"], size_eth=sizing.size_wei / 10 ** 18,
            size_rule=sizing.rule, take_profit_mc_usd=settings.DESK_TP_MC_USD, stop_loss_mc_usd=settings.DESK_SL_MC_USD,
            max_hold_h=settings.DESK_MAX_HOLD_H, decided_at=now)
        top = score["top_signals"][0]
        try:
            decision_id = await record_trade_decision(
                db, token=c["token"], side="buy", survival_probability=score["survival"],
                top_signal=f"{top['name']} {top['value']}", why=why)
        except GolemPausedError as e:
            await _drop(db, c["id"], str(e))
            continue
        await db.execute(text(
            "UPDATE desk_candidates SET stage = 'entering', decision_id = :d WHERE id = :id"
        ), {"d": decision_id, "id": c["id"]})
        await db.commit()
        try:
            tx = build_buy_tx(size_wei=sizing.size_wei, weth_reserve=mark[0], token_reserve=mark[1], weth=settings.WETH,
                              token=c["token"], wallet=settings.GOLEM_WALLET, router=settings.UNISWAP_V2_ROUTER,
                              slippage_bps=settings.DESK_SLIPPAGE_BPS, deadline=_deadline())
            tx_hash = await _signer.send(tx)
        except Exception as e:
            await _drop(db, c["id"], f"entry tx failed: {type(e).__name__}")
            continue
        await attach_trade_tx(db, decision_id, tx_hash)
        log.append(f"candidate #{c['id']} entry sent")
    return log


async def queue_candidates(db, inp, scores: dict, watching_scored: list[dict]) -> list[str]:
    open_tokens = [t.token for t in group_trades(inp.swaps) if not t.closed]
    busy = await _busy_tokens(db, open_tokens)
    queued = sum(1 for c in inp.candidates if c["stage"] in ("liquidity_check", "sizing", "entering"))
    picks = select_candidates(watching_scored, threshold=settings.DESK_ENTRY_THRESHOLD,
                              excluded=settings.desk_excluded_tokens, busy=busy, open_count=len(open_tokens),
                              queued_count=queued, max_open=settings.DESK_MAX_OPEN)
    log = []
    for p in picks:
        cid = (await db.execute(text(
            "INSERT INTO desk_candidates (token, survival) VALUES (:t, :s) RETURNING id"
        ), {"t": p["mint"].lower(), "s": p["survival"]})).scalar()
        log.append(f"candidate #{cid} queued")
    await db.commit()
    return log


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

async def dry_run_plan(db, r, inp, model: dict, scores: dict, watching_scored: list[dict], gate) -> list[str]:
    """What live mode would do this cycle, from live data, without writing or sending anything."""
    log = [f"gate {'open' if gate.allowed else 'closed: ' + str(gate.reason)} (live mode would "
           f"{'' if gate.allowed else 'not '}open positions)"]
    open_tokens = [t.token for t in group_trades(inp.swaps) if not t.closed]
    busy = await _busy_tokens(db, open_tokens)
    picks = select_candidates(watching_scored, threshold=settings.DESK_ENTRY_THRESHOLD,
                              excluded=settings.desk_excluded_tokens, busy=busy, open_count=len(open_tokens),
                              queued_count=0, max_open=settings.DESK_MAX_OPEN)
    marks, supplies, eth_usd = await _market(r, [p["mint"].lower() for p in picks])
    sizing = size_position(inp.balance_wei, size_eth=settings.DESK_POSITION_SIZE_ETH,
                           max_fraction=settings.DESK_MAX_WALLET_FRACTION, gas_reserve_eth=settings.DESK_GAS_RESERVE_ETH)
    for p in picks:
        t = p["mint"].lower()
        mark = marks.get(t)
        check = liquidity_check(weth_reserve=mark[0] if mark else None, token_reserve=mark[1] if mark else None,
                                total_supply=supplies.get(t), eth_usd=eth_usd, **_trading_params())
        mc = mc_usd(mark[0], mark[1], supplies[t], eth_usd) if check.ok else None
        verdict = (f"would enter with {sizing.size_wei / 10 ** 18:g} ETH" if check.ok and sizing.size_wei
                   else f"would drop: {check.reason or sizing.reason}")
        log.append(f"{p['mint']} survival={p['survival']:.3f} mc={mc and round(mc)} -> {verdict}")
    if not picks:
        log.append("no token at or above the entry threshold")
    log += await run_exits(db, r, inp, scores, live=False)
    return log


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

async def run_executor_cycle(db, inp) -> None:
    mode = settings.DESK_EXECUTOR_MODE
    if mode == "off":
        return
    if mode not in ("dry_run", "live"):
        print(f"[DESK EXECUTOR] Unknown DESK_EXECUTOR_MODE={mode!r}; doing nothing.", flush=True)
        return
    if mode == "live" and _signer is None:
        print("[DESK EXECUTOR] live mode requested but no signer is registered; nothing sent.", flush=True)
        return
    r = await desk_chain.reader()
    if r is None:
        print("[DESK EXECUTOR] RPC unavailable; skipping this cycle.", flush=True)
        return
    gate, model = await current_trade_gate(db)
    if model is None:
        return
    scores = await _scores(db, model["run_id"])
    watching_scored = [{"mint": w["mint"], "survival": w["survival"]} for w in inp.watching_tokens
                       if w.get("survival") is not None]

    if mode == "dry_run":
        for line in await dry_run_plan(db, r, inp, model, scores, watching_scored, gate):
            print(f"[DESK EXECUTOR][dry_run] {line}", flush=True)
        return

    log = await run_exits(db, r, inp, scores, live=True)
    log += await advance_candidates(db, r, inp, model, scores, gate.allowed)
    if gate.allowed:
        log += await queue_candidates(db, inp, scores, watching_scored)
    for line in log:
        print(f"[DESK EXECUTOR] {line}", flush=True)
