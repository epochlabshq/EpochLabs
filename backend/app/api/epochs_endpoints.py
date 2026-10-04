import json
import time
import traceback

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.api.endpoints import get_latest_model, serialize_model_run
from app.ml.jar_math import gate_thresholds
from app.services.epochs import Completion, derive_statuses, active_epoch_id, progress_for, golem_paused

router = APIRouter(prefix="/api")

# Same TTL as /api/state
_cached_epochs = None
_cached_epochs_ts = 0.0
EPOCHS_CACHE_TTL_SECONDS = 180.0


def invalidate_epochs_cache() -> None:
    global _cached_epochs, _cached_epochs_ts
    _cached_epochs = None
    _cached_epochs_ts = 0.0


async def load_completed(db: AsyncSession) -> dict[int, Completion]:
    rows = (await db.execute(text(
        "SELECT id, proof_json, completed_at FROM epochs WHERE status = 'complete' ORDER BY id"
    ))).mappings().all()
    # Raw text() queries may hand JSONB back as a string depending on the driver codec
    return {
        r["id"]: Completion(
            proof=json.loads(r["proof_json"]) if isinstance(r["proof_json"], str) else r["proof_json"],
            completed_at=r["completed_at"],
        )
        for r in rows
    }


def contracts_block() -> dict:
    return {
        "chain_id": settings.CHAIN_ID,
        "launcher": settings.EPOCH_LAUNCHER,
        "token_template": settings.EPOCH_TOKEN_TEMPLATE,
        "agent": settings.GOLEM_AGENT,
        "golem_wallet": settings.GOLEM_WALLET,
        "burn_address": settings.EPC_BURN_ADDRESS or None,
        "epc_token": settings.EPOCH_TOKEN_CA,
        "uniswap_v2_router": settings.UNISWAP_V2_ROUTER,
        "launcher_owner": settings.EPOCH_LAUNCHER_OWNER,
        "blockscout": settings.BLOCKSCOUT_BASE,
    }


def build_epochs_payload(completed: dict[int, Completion], model: dict | None, burns: dict) -> dict:
    gates = gate_thresholds()
    target = settings.AUC_TARGET
    epochs = []
    for e in derive_statuses(completed):
        c = completed.get(e["id"])
        epochs.append({
            **e,
            "progress": progress_for(e["id"], model, gates, target) if e["status"] == "active" else None,
            "proof": c.proof if c else None,
            "completed_at": c.completed_at.isoformat() if c else None,
        })
    return {
        "active": active_epoch_id(completed),
        "completed_count": len(completed),
        "golem_paused": golem_paused(model, completed, target),
        "model": model,
        "gates_config": gates,
        "target_auc": target,
        "floor_auc": settings.AUC_FLOOR,
        "epochs": epochs,
        "burns": burns,
        "contracts": contracts_block(),
    }


@router.get("/epochs")
async def get_epochs(db: AsyncSession = Depends(get_db)):
    """
    GET /api/epochs - Status of the six post-mainnet epochs.
    Status is computed from the `epochs` table (written only by the Epoch Watcher) and the latest model run,
    which is read through the same helper as /api/state. Nothing here is set by hand.
    """
    global _cached_epochs, _cached_epochs_ts
    if _cached_epochs is not None and time.time() - _cached_epochs_ts < EPOCHS_CACHE_TTL_SECONDS:
        return _cached_epochs

    try:
        completed = await load_completed(db)
        latest = await get_latest_model(db)
        burn_row = (await db.execute(text(
            "SELECT COALESCE(SUM(amount_wei), 0)::text AS total, COUNT(*) AS n FROM epc_burns WHERE burn_address = :b"
        ), {"b": settings.EPC_BURN_ADDRESS.lower()})).mappings().one()
    except Exception as e:
        print(f"[API ERROR] get_epochs failed: {e}\n{traceback.format_exc()}", flush=True)
        # Never fall back to invented statuses
        raise HTTPException(status_code=503, detail="Epoch data unavailable")

    payload = build_epochs_payload(
        completed,
        serialize_model_run(latest) if latest else None,
        {"total_wei": burn_row["total"], "count": burn_row["n"]},
    )
    _cached_epochs = payload
    _cached_epochs_ts = time.time()
    return payload
