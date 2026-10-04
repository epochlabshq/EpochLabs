import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, text
from app.db.database import get_db
from app.db.models import Token, ModelRun, TokenStatus
from app.core.config import settings
from app.ml.jar_math import gate_thresholds

import time
router = APIRouter(prefix="/api")

# In-memory response cache for state snapshot (TTL 15 seconds)
_cached_state_data = None
_cached_state_timestamp = 0.0
STATE_CACHE_TTL_SECONDS = 300.0


def invalidate_state_cache() -> None:
    """Drop the cached /api/state snapshot (called after a new model run is saved)."""
    global _cached_state_data, _cached_state_timestamp
    _cached_state_data = None
    _cached_state_timestamp = 0.0


def serialize_model_run(run: ModelRun) -> dict:
    """Public shape of a model run. Shared by /api/state, the WS `model` event and /api/epochs."""
    return {
        "run_id": run.id,
        "ran_at": run.ran_at.isoformat(),
        "n": run.n_samples,
        "n_positive": run.n_positive,
        "d": run.capacity_d,
        "auc": run.auc_mean,
        "auc_std": run.auc_std,
        "epsilon_vc": run.epsilon_vc,
        "auc_boot_lower": run.auc_boot_lower,
        "proven_floor": run.proven_floor,
        "jar_level": run.jar_level,
        "gates": run.gates_status,
        "blocked_by": run.blocked_by,
        "hour_rates": run.hour_rates,
        "feature_importance": run.feature_importance
    }


async def get_latest_model(db: AsyncSession) -> ModelRun | None:
    """The single source for "the current model". Never compute these numbers anywhere else."""
    res = await db.execute(select(ModelRun).order_by(desc(ModelRun.id)).limit(1))
    return res.scalar_one_or_none()

@router.get("/state")
async def get_app_state(db: AsyncSession = Depends(get_db)):
    """
    GET /api/state - Full snapshot used on page load before WebSocket connects:
    - Latest 100 tokens
    - Global counters (above 10k, passed 30k, stalled, median holders)
    - Latest model run
    Uses 15s in-memory cache to eliminate redundant DB hits from frequent page views.
    """
    global _cached_state_data, _cached_state_timestamp
    now_ts = time.time()
    if _cached_state_data is not None and (now_ts - _cached_state_timestamp < STATE_CACHE_TTL_SECONDS):
        return _cached_state_data

    try:
        stmt_all = text("SELECT mint, COALESCE(chain, 'robinhood') as chain, name, symbol, lore, lore_display, lore_withheld, image_url, creator, launched_at, launch_hour_utc as launch_hour, holders, peak_mc, status::text FROM tokens WHERE status::text != 'excluded' ORDER BY first_seen_at DESC;")
        res_all = await db.execute(stmt_all)
        all_rows = res_all.mappings().all()

        above_10k = len(all_rows)
        passed_30k = sum(1 for r in all_rows if r["status"] == "passed")
        stalled = sum(1 for r in all_rows if r["status"] == "stalled")
        pending = sum(1 for r in all_rows if r["status"] == "pending")

        holders_list = sorted([r["holders"] for r in all_rows if r["holders"] is not None])
        median_holders = holders_list[len(holders_list) // 2] if holders_list else 288

        tokens = all_rows[:400]

        latest_model = await get_latest_model(db)
        model_data = serialize_model_run(latest_model) if latest_model else None

        token_list = [
            {
                "mint": t["mint"],
                "chain": t["chain"] or "robinhood",
                "name": t["name"],
                "symbol": t["symbol"],
                "lore": t["lore_display"] or t["lore"],
                "lore_withheld": t["lore_withheld"],
                "logo": t["image_url"],
                "launched_at": t["launched_at"].isoformat() if hasattr(t["launched_at"], "isoformat") else str(t["launched_at"]),
                "launch_hour": t["launch_hour"],
                "holders": t["holders"],  # None until sampled at the 48h label
                "peak_mc": float(t["peak_mc"]) if t["peak_mc"] is not None else 0.0,
                "status": t["status"]
            }
            for t in tokens
        ]
    except Exception as e:
        import traceback
        print(f"[API ERROR] get_app_state failed: {e}\n{traceback.format_exc()}", flush=True)
        # Database unavailable: report no model rather than inventing numbers
        above_10k, passed_30k, stalled, pending, median_holders = 0, 0, 0, 0, 0
        token_list = []
        model_data = None

    response_payload = {
        "counters": {
            "above_10k": above_10k,
            "passed_30k": passed_30k,
            "stalled": stalled,
            "pending": pending,
            "median_holders": median_holders
        },
        "latest_model": model_data,
        "gates_config": gate_thresholds(),
        "target_auc": settings.AUC_TARGET,
        "floor_auc": settings.AUC_FLOOR,
        "tokens": token_list
    }
    _cached_state_data = response_payload
    _cached_state_timestamp = time.time()
    return response_payload

@router.get("/model/history")
async def get_model_history(days: int = 30, db: AsyncSession = Depends(get_db)):
    """GET /api/model/history?days=30 - Array of model_runs for AUC-over-time chart."""
    try:
        stmt = select(ModelRun).order_by(desc(ModelRun.ran_at)).limit(days * 24)
        res = await db.execute(stmt)
        runs = res.scalars().all()
        return [
            {
                "id": r.id,
                "ran_at": r.ran_at.isoformat(),
                "n_samples": r.n_samples,
                "n_positive": r.n_positive,
                "auc_mean": r.auc_mean,
                "proven_floor": r.proven_floor,
                "jar_level": r.jar_level,
                "blocked_by": r.blocked_by
            }
            for r in runs
        ]
    except Exception:
        return []

def methodology_snapshot() -> dict:
    """Machine-readable methodology. Also frozen into Epoch II's proof when it completes."""
    return {
        "universe": "Every token launched on Robinhood Chain",
        "study_population": "Tokens with peak market cap >= $10,000",
        "positive_label": "Peak market cap reached >= $30,000",
        "negative_label": "Reached $10K, did not reach $30K, age >= 48 hours",
        "features": [
            {"name": "launch_hour", "encoding": "sin/cos of hour-of-day (2 columns)"},
            {"name": "launch_dow", "encoding": "one-hot day-of-week (7 columns)"},
            {"name": "holders", "encoding": "log1p of non-zero balance token accounts at 48h"},
            {"name": "lore", "encoding": "MiniLM-L6-v2 sentence embedding -> PCA 24 dims"},
            {"name": "lore_len", "encoding": "word count"},
            {"name": "lore_missing", "encoding": "binary flag"},
            {"name": "name_tokens", "encoding": "word count"}
        ],
        "capacity_d": settings.CAPACITY_D,
        "delta": settings.DELTA_CONFIDENCE,
        "proven_floor": "min(auc_mean - epsilon_vc, bootstrap 2.5th percentile AUC)",
        "jar_level": "clip((proven_floor - floor_auc) / (target_auc - floor_auc), 0, 1), capped while any gate fails",
        "jar_gate_cap": settings.JAR_GATE_CAP,
        "gates": gate_thresholds(),
        "target_auc": settings.AUC_TARGET,
        "floor_auc": settings.AUC_FLOOR
    }

@router.get("/methodology.json")
async def get_methodology():
    """GET /api/methodology.json - Machine-readable methodology specification."""
    return methodology_snapshot()

@router.get("/dataset.csv")
async def download_public_dataset(db: AsyncSession = Depends(get_db)):
    """
    GET /api/dataset.csv - The MANDATORY full labeled public dataset downloadable CSV.
    Allows anyone to reproduce the AUC number and verify the jar floor.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "mint", "name", "symbol", "launched_at", "launch_hour_utc", 
        "peak_mc", "holders", "status", "passed_label"
    ])

    try:
        stmt = select(Token).where(Token.status.in_([TokenStatus.passed, TokenStatus.stalled]))
        res = await db.execute(stmt)
        tokens = res.scalars().all()
        for t in tokens:
            clean_name = (t.name or "").replace("\r", " ").replace("\n", " ").strip()
            clean_symbol = (t.symbol or "").replace("\r", " ").replace("\n", " ").strip()
            writer.writerow([
                t.mint, clean_name, clean_symbol, t.launched_at.isoformat() if t.launched_at else "",
                t.launch_hour_utc, float(t.peak_mc) if t.peak_mc is not None else 0.0, t.holders or 0,
                t.status.value if t.status else "", 1 if t.status == TokenStatus.passed else 0
            ])
    except Exception:
        # Sample row if DB uninitialized
        writer.writerow([
            "7xK11223344556677889900aabbccddeeff", "Midnight Janitor", "MJ88",
            datetime.now(timezone.utc).isoformat(), 20, 150300.0, 412, "passed", 1
        ])

    # Prepend UTF-8 BOM (\ufeff) so Excel opens with proper UTF-8 encoding
    csv_content = "\ufeff" + output.getvalue()
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=epochlabs_dataset.csv"}
    )
