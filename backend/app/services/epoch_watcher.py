"""
Epoch Watcher: every EPOCH_WATCHER_INTERVAL_SECONDS, index onchain evidence, then check the active epoch's
trigger and record its completion (with proof). Completions cascade in one tick, so history backfills itself.
"""
import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.epochs_schema import ensure_epochs_schema
from app.db.locks import exclusive
from app.api.endpoints import methodology_snapshot
from app.api.epochs_endpoints import load_completed, invalidate_epochs_cache
from app.api.websocket import manager
from app.services.chain_reader import ChainReader
from app.services.epoch_indexer import run_indexers
from app.services.epochs import (
    ChainEvent, Completion, active_epoch_id, agent_signed_launches, assert_can_complete,
    first_event_after, tx_proof, validate_proof,
)
from app.services.model_worker import MODEL_WORKER_NOTE_PREFIX

WATCHER_LOCK_KEY = 0x45504F43_57415443  # "EPOC" "WATC"


# ---------------------------------------------------------------------------
# Trigger checkers: each returns the earliest qualifying evidence at/after `not_before`, or None
# ---------------------------------------------------------------------------

async def _first_model_run(db, condition: str, params: dict, not_before: Optional[datetime]) -> Optional[dict]:
    # Only runs produced by the model worker with the current capacity count as evidence;
    # older runs were written by one-off scripts.
    row = (await db.execute(text(
        "SELECT id, ran_at FROM model_runs "
        "WHERE notes LIKE :prefix AND capacity_d = :d AND " + condition +
        " AND (CAST(:nb AS TIMESTAMPTZ) IS NULL OR ran_at >= CAST(:nb AS TIMESTAMPTZ)) ORDER BY id LIMIT 1"
    ), {"prefix": f"{MODEL_WORKER_NOTE_PREFIX}%", "d": settings.CAPACITY_D, "nb": not_before, **params})).first()
    return {"run_id": row[0], "ran_at": row[1]} if row else None


async def check_ingestion(db, not_before) -> Optional[Completion]:
    run = await _first_model_run(db, "n_samples >= :min", {"min": settings.GATE_N_SAMPLES}, not_before)
    if not run:
        return None
    return Completion({"type": "model_run", "run_id": run["run_id"]}, run["ran_at"])


async def check_hourglass_fill(db, not_before) -> Optional[Completion]:
    run = await _first_model_run(db, "proven_floor >= :t AND blocked_by IS NULL", {"t": settings.AUC_TARGET}, not_before)
    if not run:
        return None
    return Completion(
        {"type": "model_run", "run_id": run["run_id"], "methodology": methodology_snapshot()},
        run["ran_at"],
    )


async def check_first_trade(db, not_before) -> Optional[Completion]:
    rows = (await db.execute(text("SELECT tx_hash, block, at FROM golem_swaps ORDER BY block"))).all()
    ev = first_event_after((ChainEvent(r[0], r[1], 0, r[2]) for r in rows), not_before)
    if not ev:
        return None
    return Completion(tx_proof(ev.tx_hash, settings.BLOCKSCOUT_BASE), ev.at)


async def check_first_burn(db, not_before) -> Optional[Completion]:
    if not settings.EPC_BURN_ADDRESS:
        return None  # Burn rules not published yet
    rows = (await db.execute(text(
        "SELECT tx_hash, block, log_index, at, amount_wei::text FROM epc_burns "
        "WHERE burn_address = :b ORDER BY block, log_index"
    ), {"b": settings.EPC_BURN_ADDRESS.lower()})).all()
    ev = first_event_after((ChainEvent(r[0], r[1], r[2], r[3], {"amount_wei": r[4]}) for r in rows), not_before)
    if not ev:
        return None
    return Completion(tx_proof(ev.tx_hash, settings.BLOCKSCOUT_BASE, amount_wei=ev.data["amount_wei"]), ev.at)


async def check_golem_launch(db, not_before) -> Optional[Completion]:
    rows = (await db.execute(text(
        "SELECT tx_hash, block, log_index, at, kind, token, new_agent FROM launcher_events ORDER BY block, log_index"
    ))).all()
    events = [ChainEvent(r[0], r[1], r[2], r[3], {"kind": r[4], "token": r[5], "new_agent": r[6]}) for r in rows]
    ev = first_event_after(agent_signed_launches(events, settings.GOLEM_AGENT), not_before)
    if not ev:
        return None
    return Completion(
        tx_proof(ev.tx_hash, settings.BLOCKSCOUT_BASE, token_address=ev.data["token"],
                 token_url=f"{settings.BLOCKSCOUT_BASE.rstrip('/')}/address/{ev.data['token']}"),
        ev.at,
    )


async def check_open_golem(db, not_before) -> Optional[Completion]:
    repo, tag = settings.GOLEM_GITHUB_REPO, settings.GOLEM_RELEASE_TAG
    if not repo:
        return None  # Repo not public yet
    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as gh:
        rel = await gh.get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
        if rel.status_code == 404:
            return None
        rel.raise_for_status()
        release = rel.json()
        if release.get("draft"):
            return None
        lic = await gh.get(f"https://api.github.com/repos/{repo}/license")
        if lic.status_code == 404:
            return None  # A release without a LICENSE does not count
        lic.raise_for_status()
    published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
    if not_before is not None and published < not_before:
        return None
    return Completion(
        {"type": "release", "url": f"https://github.com/{repo}/releases/tag/{tag}",
         "license": (lic.json().get("license") or {}).get("spdx_id")},
        published,
    )


CHECKERS = {
    1: check_ingestion,
    2: check_hourglass_fill,
    3: check_first_trade,
    4: check_first_burn,
    5: check_golem_launch,
    6: check_open_golem,
}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

async def record_completion(db, epoch_id: int, completed: dict[int, Completion], completion: Completion) -> None:
    assert_can_complete(epoch_id, completed, completion)
    run_id = completion.proof.get("run_id")
    known_run_ids = []
    if isinstance(run_id, int):
        exists = (await db.execute(text("SELECT 1 FROM model_runs WHERE id = :i"), {"i": run_id})).first()
        known_run_ids = [run_id] if exists else []
    validate_proof(
        epoch_id, completion.proof,
        blockscout_base=settings.BLOCKSCOUT_BASE,
        known_run_ids=known_run_ids,
        github_repo=settings.GOLEM_GITHUB_REPO,
        release_tag=settings.GOLEM_RELEASE_TAG,
    )
    res = await db.execute(text(
        "UPDATE epochs SET status = 'complete', proof_json = CAST(:p AS JSONB), completed_at = :at "
        "WHERE id = :id AND status = 'locked'"
    ), {"p": json.dumps(completion.proof), "at": completion.completed_at, "id": epoch_id})
    if res.rowcount != 1:
        raise RuntimeError(f"Epoch {epoch_id} row missing or already complete")
    await db.commit()


async def run_watcher_cycle(reader: Optional[ChainReader]) -> list[int]:
    """One tick. Returns the ids of epochs completed during it."""
    newly_completed: list[int] = []
    async with exclusive(WATCHER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            return []
        try:
            if reader is not None:
                try:
                    await run_indexers(db, reader)
                except Exception as e:
                    # RPC trouble only delays onchain epochs; model-based epochs still advance
                    await db.rollback()
                    print(f"[EPOCH WATCHER] Indexing failed: {e}", flush=True)

            completed = await load_completed(db)
            while (active := active_epoch_id(completed)) is not None:
                prev = completed.get(active - 1)
                completion = await CHECKERS[active](db, prev.completed_at if prev else None)
                if completion is None:
                    break
                await record_completion(db, active, completed, completion)
                completed[active] = completion
                newly_completed.append(active)
                print(f"[EPOCH WATCHER] Epoch {active} complete: {completion.proof}", flush=True)
        finally:
            await db.rollback()

    if newly_completed:
        invalidate_epochs_cache()
        for epoch_id in newly_completed:
            c = completed[epoch_id]
            await manager.broadcast({"epoch": {
                "id": epoch_id, "status": "complete", "proof": c.proof, "completed_at": c.completed_at.isoformat(),
            }})
    return newly_completed


async def start_epoch_watcher_loop():
    if not settings.EPOCH_WATCHER_ENABLED:
        print("[EPOCH WATCHER] Disabled via EPOCH_WATCHER_ENABLED.")
        return
    async with AsyncSessionLocal() as db:
        await ensure_epochs_schema(db)

    reader = ChainReader(settings.CHAIN_RPC_URL) if settings.CHAIN_RPC_URL else None
    chain_verified = False

    print(f"[EPOCH WATCHER] Started (interval {settings.EPOCH_WATCHER_INTERVAL_SECONDS}s).")
    while True:
        # Onchain evidence is only read from an RPC proven to serve chain CHAIN_ID
        if reader is not None and not chain_verified:
            try:
                chain_id = await reader.chain_id()
                chain_verified = chain_id == settings.CHAIN_ID
                if not chain_verified:
                    print(f"[EPOCH WATCHER] RPC chain id {chain_id} != {settings.CHAIN_ID}; onchain epochs paused.")
            except Exception as e:
                print(f"[EPOCH WATCHER] RPC unreachable: {e}")
        try:
            await run_watcher_cycle(reader if chain_verified else None)
        except Exception as e:
            print(f"[EPOCH WATCHER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(settings.EPOCH_WATCHER_INTERVAL_SECONDS)
