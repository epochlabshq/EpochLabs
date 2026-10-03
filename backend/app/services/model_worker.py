import asyncio
import traceback

import pandas as pd
from sqlalchemy import text

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.locks import exclusive
from app.db.models import ModelRun
from app.db.model_artifacts_schema import ensure_model_artifacts_schema
from app.ml.artifact import serialize_artifact
from app.api.endpoints import get_latest_model, serialize_model_run, invalidate_state_cache
from app.api.websocket import manager

# Runs written by this worker start with this note. Only these count as evidence for Epochs I and II.
MODEL_WORKER_NOTE_PREFIX = "model_worker"

# Arbitrary constant key so only one instance trains at a time (Railway may run replicas)
MODEL_WORKER_LOCK_KEY = 0x45504F43_4D4F444C  # "EPOC" "MODL"


def needs_retrain(latest: ModelRun | None, n_samples: int, n_positive: int, has_artifact: bool = True) -> bool:
    """
    Retrain only when something the published numbers depend on has changed:
    no run yet, the labeled set moved, or the run was produced with a different capacity d
    (i.e. by an older formula). Also when the latest run has no stored artifact, since live scoring
    must use the model behind the published run.
    """
    if latest is None:
        return True
    if latest.capacity_d != settings.CAPACITY_D or not has_artifact:
        return True
    return latest.n_samples != n_samples or latest.n_positive != n_positive


async def _load_labeled_frame(db) -> pd.DataFrame:
    res = await db.execute(text(
        "SELECT mint, name, lore, launched_at, launch_hour_utc, holders, peak_mc, status::text AS status "
        "FROM tokens WHERE status::text IN ('passed', 'stalled');"
    ))
    df = pd.DataFrame(res.mappings().all())
    if df.empty:
        return df
    df["launch_hour_utc"] = df["launch_hour_utc"].astype(float)
    # Holders stay missing when not sampled; extract_features maps missing to 0. No imputation.
    df["holders"] = df["holders"].astype(float)
    df["peak_mc"] = df["peak_mc"].astype(float)
    return df


async def run_model_cycle(force: bool = False) -> dict | None:
    """Train on the current labeled set, persist a ModelRun and broadcast it. Returns the new run's payload, if any."""
    from app.ml.trainer import train_model_and_evaluate

    async with exclusive(MODEL_WORKER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            print("[MODEL WORKER] Another instance holds the training lock. Skipping.")
            return None
        try:
            await ensure_model_artifacts_schema(db)
            counts = (await db.execute(text(
                "SELECT COUNT(*) FILTER (WHERE status::text IN ('passed','stalled')) AS n, "
                "COUNT(*) FILTER (WHERE status::text = 'passed') AS pos FROM tokens;"
            ))).mappings().one()
            latest = await get_latest_model(db)
            has_artifact = latest is not None and (await db.execute(
                text("SELECT 1 FROM model_artifacts WHERE run_id = :id"), {"id": latest.id}
            )).first() is not None
            if not force and not needs_retrain(latest, counts["n"], counts["pos"], has_artifact):
                return None

            df = await _load_labeled_frame(db)
            print(f"[MODEL WORKER] Training on {len(df)} labeled tokens...")
            # CPU-bound (CV + 2000 bootstrap resamples): keep the event loop responsive
            result = await asyncio.to_thread(train_model_and_evaluate, df)
            if "error" in result:
                print(f"[MODEL WORKER] Training skipped: {result['error']}")
                return None

            run = ModelRun(
                n_samples=result["n_samples"],
                n_positive=result["n_positive"],
                capacity_d=result["capacity_d"],
                auc_mean=result["auc_mean"],
                auc_std=result["auc_std"],
                epsilon_vc=result["epsilon_vc"],
                auc_boot_lower=result["auc_boot_lower"],
                proven_floor=result["proven_floor"],
                jar_level=result["jar_level"],
                gates_status=result["gates"],
                blocked_by=result["blocked_by"],
                hour_rates=result.get("hour_rates", {}),
                feature_importance=result.get("feature_importance", {}),
                notes=f"{MODEL_WORKER_NOTE_PREFIX} · time_split_gap={result.get('time_split_gap')}",
            )
            db.add(run)
            await db.flush()  # assigns run.id; run and artifact commit together
            raw, digest = serialize_artifact(result["artifact"])
            await db.execute(text(
                "INSERT INTO model_artifacts (run_id, sha256, artifact) VALUES (:id, :sha, :raw)"
            ), {"id": run.id, "sha": digest, "raw": raw})
            await db.commit()
            await db.refresh(run)
            # Serialize while attached; the rollback below expires ORM state
            payload = serialize_model_run(run)
        finally:
            await db.rollback()

    invalidate_state_cache()
    from app.api.epochs_endpoints import invalidate_epochs_cache
    invalidate_epochs_cache()  # Epoch I/II progress must match /api/state exactly
    await manager.broadcast({"model": payload})
    print(
        f"[MODEL WORKER] Saved run {payload['run_id']}: n={payload['n']} auc={payload['auc']} "
        f"floor={payload['proven_floor']} jar={payload['jar_level']} blocked_by={payload['blocked_by']} "
        f"artifact_sha256={digest}"
    )
    return payload


async def start_model_worker_loop():
    if not settings.MODEL_WORKER_ENABLED:
        print("[MODEL WORKER] Disabled via MODEL_WORKER_ENABLED.")
        return
    print(f"[MODEL WORKER] Started (interval {settings.MODEL_WORKER_INTERVAL_SECONDS}s).")
    while True:
        try:
            await run_model_cycle()
        except Exception as e:
            print(f"[MODEL WORKER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(settings.MODEL_WORKER_INTERVAL_SECONDS)
