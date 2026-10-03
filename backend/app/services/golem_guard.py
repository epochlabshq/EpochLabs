"""
Golem trading guard. Any trade executor MUST call `record_trade_decision` before sending a swap:
it refuses a buy unless Epoch II is complete and the live model still passes every gate with a floor at target.
Epoch II stays complete forever, but trading pauses whenever the live model regresses.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from app.core.config import settings
from app.api.endpoints import get_latest_model, serialize_model_run
from app.api.epochs_endpoints import load_completed
from app.services.desk import EXIT_REASONS, canonical_json, why_hash
from app.services.epochs import Completion, golem_paused


class GolemPausedError(RuntimeError):
    pass


@dataclass(frozen=True)
class TradeGate:
    allowed: bool
    reason: Optional[str]  # None when allowed


def trade_gate(completed: dict[int, Completion], model: Optional[dict], target_auc: float) -> TradeGate:
    if 2 not in completed:
        return TradeGate(False, "epoch_ii_not_complete")
    paused = golem_paused(model, completed, target_auc)
    if paused:
        return TradeGate(False, paused)
    return TradeGate(True, None)


async def current_trade_gate(db) -> tuple[TradeGate, Optional[dict]]:
    completed = await load_completed(db)
    latest = await get_latest_model(db)
    model = serialize_model_run(latest) if latest else None
    return trade_gate(completed, model, settings.AUC_TARGET), model


async def record_trade_decision(db, *, token: str, side: str, survival_probability: float, top_signal: str,
                                why: Optional[dict] = None, exit_reason: Optional[str] = None) -> int:
    """
    Log a decision. Returns the decision id. A buy raises GolemPausedError while the gate is closed; a sell
    does not, so open positions can still exit while Golem is paused.
    A buy must carry its Why card (app.services.desk.build_why); it is stored once as canonical JSON with its
    sha256, which is also written to the log so the public record predates the outcome.
    A sell must say which exit rule fired.
    """
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    gate, model = await current_trade_gate(db)
    # Only entries are gated: while paused, open positions still run to their exit rule
    if side == "buy" and not gate.allowed:
        raise GolemPausedError(f"Golem paused: {gate.reason}")
    if model is None:
        raise GolemPausedError("Golem paused: no_model_run")
    if not 0.0 <= survival_probability <= 1.0:
        raise ValueError("survival_probability must be within [0, 1]")
    if side == "buy" and why is None:
        raise ValueError("a buy needs its Why card")
    if side == "sell" and exit_reason not in EXIT_REASONS:
        raise ValueError(f"a sell needs an exit_reason in {EXIT_REASONS}")
    if why is not None and why.get("model_run_id") != model["run_id"]:
        raise ValueError("Why card was built from a different model run than the live one")
    canonical = canonical_json(why) if why is not None else None
    digest = why_hash(canonical) if canonical else None
    row = (await db.execute(text(
        "INSERT INTO golem_trade_decisions "
        "(token, side, survival_probability, top_signal, run_id, why_canonical, why_sha256, exit_reason) "
        "VALUES (:token, :side, :p, :signal, :run, :why, :sha, :exit) RETURNING id"
    ), {"token": token.lower(), "side": side, "p": survival_probability, "signal": top_signal,
        "run": model["run_id"], "why": canonical, "sha": digest, "exit": exit_reason})).first()
    await db.commit()
    if digest:
        print(f"[GOLEM] decision {row[0]} {side} why_sha256={digest}", flush=True)
    return row[0]


async def attach_trade_tx(db, decision_id: int, tx_hash: str) -> None:
    """Link a decision to the swap tx it produced (the indexer fills in the onchain side)."""
    res = await db.execute(text(
        "UPDATE golem_trade_decisions SET tx_hash = :h WHERE id = :id AND tx_hash IS NULL"
    ), {"h": tx_hash.lower(), "id": decision_id})
    if res.rowcount != 1:
        raise RuntimeError(f"Decision {decision_id} missing or already linked to a tx")
    await db.commit()
