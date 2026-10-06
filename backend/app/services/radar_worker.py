"""
Radar worker. Clusters the lore of every mapped token once a day (RADAR_RUN_HOUR_UTC:RADAR_RUN_MINUTE_UTC, 00:15 UTC).

Between runs the loop sleeps until the next scheduled time and does no database work. A run needs the MiniLM
embedder: without it the run is skipped (clusters from zero vectors would be fiction). The three result tables are
written in one transaction, so the API never sees a half-written run.
"""
import asyncio
import json
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.api.radar_endpoints import invalidate_radar_cache
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.locks import exclusive
from app.db.radar_schema import ensure_radar_schema
from app.services.radar import PrevCluster, RadarParams, RunResult, build_run
from app.services.radar_status import set_status

RADAR_WORKER_LOCK_KEY = 0x45504F43_52414452  # "EPOC" "RADR"
RETRY_AFTER_FAILURE_SECONDS = 3600
EMBED_TIMEOUT_S = 900.0  # model download plus embedding; past this the run fails instead of hanging


def last_scheduled(now: datetime, hour: int, minute: int) -> datetime:
    """The most recent scheduled run time at or before `now`."""
    today = now.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today if today <= now else today - timedelta(days=1)


def needs_run(now: datetime, latest_run_at: Optional[datetime], hour: int, minute: int) -> bool:
    """No run yet, or the newest run is older than the latest scheduled slot."""
    return latest_run_at is None or latest_run_at < last_scheduled(now, hour, minute)


def seconds_until_next(now: datetime, hour: int, minute: int) -> float:
    return (last_scheduled(now, hour, minute) + timedelta(days=1) - now).total_seconds()


async def _load_tokens(db) -> tuple[pd.DataFrame, int]:
    """(tokens with lore, number of tokens without lore). Excluded tokens are ignored."""
    where = "status::text IN ('pending', 'passed', 'stalled')"
    params: dict = {}
    if settings.RADAR_HISTORY_DAYS > 0:
        where += " AND launched_at > :since"
        params["since"] = datetime.now(timezone.utc) - timedelta(days=settings.RADAR_HISTORY_DAYS)
    res = await db.execute(text(
        f"SELECT mint, lore, lore_withheld, launched_at, peak_mc, status::text AS status FROM tokens WHERE {where} "
        "ORDER BY mint"), params)
    df = pd.DataFrame(res.mappings().all())
    if df.empty:
        return df, 0
    has_lore = df["lore"].fillna("").astype(str).str.strip() != ""
    return df[has_lore].reset_index(drop=True), int((~has_lore).sum())


FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # the same MiniLM the brief names, 384 dimensions
_fast_model = None


def _embed(lore: list[str]) -> Optional[tuple[np.ndarray, str]]:
    """
    (embeddings, embedder label), or None when no embedder is installed.
    sentence-transformers first when present. Production runs without it (PyTorch ran the Railway box out of
    memory), so the same MiniLM is also read through ONNX with fastembed, which needs no torch. Only the Radar
    uses this path: the model worker's own features are left exactly as they were.
    """
    global _fast_model
    from app.ml.features import get_sentence_model
    model = get_sentence_model()
    if model is not None:
        return np.asarray(model.encode(lore, batch_size=64, show_progress_bar=False), dtype=float), "all-MiniLM-L6-v2"
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None
    if _fast_model is None:
        _fast_model = TextEmbedding(FASTEMBED_MODEL)
    return np.asarray(list(_fast_model.embed(lore, batch_size=64)), dtype=float), "all-MiniLM-L6-v2 (ONNX)"


async def _persist(db, result: RunResult) -> None:
    run = result.run
    rid = run["run_id"]
    # A forced re-run on the same day replaces that day's rows
    for table in ("radar_points", "radar_clusters", "radar_runs"):
        await db.execute(text(f"DELETE FROM {table} WHERE run_id = :r"), {"r": rid})
    await db.execute(text(
        "INSERT INTO radar_runs (run_id, run_at, n_tokens, n_resolved, baseline_rate, method, params_json) "
        "VALUES (:run_id, :run_at, :n_tokens, :n_resolved, :baseline_rate, :method, CAST(:params AS JSONB))"),
        {**{k: run[k] for k in ("run_id", "run_at", "n_tokens", "n_resolved", "baseline_rate", "method")},
         "params": json.dumps(run["params_json"])})
    rows = [{**c, "run_id": rid} for c in result.clusters]
    rows.append({"run_id": rid, "cluster_id": "unclustered", "label": "Unclustered", "keywords": [],
                 "label_source": "keywords", "centroid_x": None, "centroid_y": None, "centroid_vec": None,
                 **result.unclustered})
    await db.execute(text(
        "INSERT INTO radar_clusters (run_id, cluster_id, label, keywords, label_source, n_total, n_resolved, "
        "reached_30k, survival_rate, ci_low, ci_high, lift, median_peak_mc, launches_7d, trend_pct, share_7d, "
        "survival_7d, survival_30d, saturation, centroid_x, centroid_y, centroid_vec) "
        "VALUES (:run_id, :cluster_id, :label, CAST(:keywords AS TEXT[]), :label_source, :n_total, :n_resolved, "
        ":reached_30k, :survival_rate, :ci_low, :ci_high, :lift, :median_peak_mc, :launches_7d, :trend_pct, "
        ":share_7d, :survival_7d, :survival_30d, :saturation, :centroid_x, :centroid_y, "
        "CAST(:centroid_vec AS DOUBLE PRECISION[]))"), rows)
    for i in range(0, len(result.points), 1000):
        await db.execute(text(
            "INSERT INTO radar_points (run_id, token_address, cluster_id, x, y, resolved, reached_30k, launched_at) "
            "VALUES (:run_id, :token_address, :cluster_id, :x, :y, :resolved, :reached_30k, :launched_at)"),
            [{**p, "run_id": rid} for p in result.points[i:i + 1000]])
    await db.commit()


async def run_radar_cycle(force: bool = False) -> Optional[dict]:
    """One clustering run. Returns the stored run row, or None when skipped."""
    async with exclusive(RADAR_WORKER_LOCK_KEY) as locked, AsyncSessionLocal() as db:
        if not locked:
            set_status("waiting", "another instance holds the lock")
            return None
        try:
            await ensure_radar_schema(db)
            now = datetime.now(timezone.utc)
            latest = (await db.execute(text("SELECT MAX(run_at) FROM radar_runs"))).scalar()
            if not force and not needs_run(now, latest, settings.RADAR_RUN_HOUR_UTC, settings.RADAR_RUN_MINUTE_UTC):
                set_status("idle", f"today's run exists (last {latest:%Y-%m-%d %H:%M} UTC)")
                return None
            set_status("running", "loading tokens")

            df, n_lore_missing = await _load_tokens(db)
            if df.empty:
                set_status("skipped", "no tokens with lore yet")
                return None
            rid = f"radar_{now:%Y-%m-%d}"
            prev_rows = (await db.execute(text(
                "SELECT cluster_id, centroid_vec FROM radar_clusters WHERE centroid_vec IS NOT NULL AND run_id = "
                "(SELECT run_id FROM radar_runs WHERE run_id <> :r ORDER BY run_at DESC LIMIT 1)"), {"r": rid})
            ).mappings().all()
            prev = [PrevCluster(r["cluster_id"], np.asarray(r["centroid_vec"], dtype=float)) for r in prev_rows]
            max_id = (await db.execute(text(
                "SELECT COALESCE(MAX(CAST(SUBSTRING(cluster_id FROM 3) AS INTEGER)), 0) FROM radar_clusters "
                "WHERE cluster_id ~ '^c_[0-9]+$'"))).scalar()
            await db.rollback()  # nothing below needs the read transaction while the CPU-heavy part runs

            set_status("running", f"embedding {len(df)} lore texts (the first run downloads the model)")
            embedded = await asyncio.wait_for(
                asyncio.to_thread(_embed, df["lore"].astype(str).tolist()), EMBED_TIMEOUT_S)
            if embedded is None:
                set_status("skipped", "no embedder installed (sentence-transformers or fastembed), "
                                      "no clusters are made from empty vectors")
                return None
            embeddings, embedder = embedded
            set_status("running", f"clustering {len(df)} tokens ({embedder})")
            df["launched_at"] = pd.to_datetime(df["launched_at"], utc=True)
            result = await asyncio.to_thread(
                build_run, df, embeddings, prev, int(max_id or 0) + 1, now, RadarParams.from_settings(),
                n_lore_missing=n_lore_missing, embedder=embedder)
            set_status("running", "saving")
            await _persist(db, result)
        except Exception as e:
            await db.rollback()
            set_status("failed", f"{type(e).__name__}: {e}")
            raise

    invalidate_radar_cache()
    r = result.run
    set_status("ok", f"{r['run_id']}: {len(result.clusters)} narratives from {r['n_tokens']} tokens")
    print(f"[RADAR WORKER] Saved {r['run_id']}: tokens={r['n_tokens']} resolved={r['n_resolved']} "
          f"method={r['method']} projection={r['params_json']['projection']} clusters={len(result.clusters)}")
    return r


async def start_radar_worker_loop():
    if not settings.RADAR_WORKER_ENABLED:
        set_status("disabled", "RADAR_WORKER_ENABLED is off")
        return
    h, m = settings.RADAR_RUN_HOUR_UTC, settings.RADAR_RUN_MINUTE_UTC
    set_status("waiting", f"started, first check in 90s (daily at {h:02d}:{m:02d} UTC)")
    await asyncio.sleep(90)  # let startup settle: the first run loads the embedder
    while True:
        wait = seconds_until_next(datetime.now(timezone.utc), h, m)
        try:
            await run_radar_cycle()
        except Exception as e:
            print(f"[RADAR WORKER] Cycle failed: {e}\n{traceback.format_exc()}", flush=True)
            wait = min(wait, RETRY_AFTER_FAILURE_SECONDS)
        await asyncio.sleep(max(60.0, wait))
