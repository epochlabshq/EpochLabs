"""
Meta Radar API. Every number comes from the latest stored run (radar_runs / radar_clusters / radar_points).

The honesty rules live in the serializers below, so no client can show something the API did not allow:
- a narrative with fewer than RADAR_MIN_RESOLVED resolved tokens carries no percentage at all,
- points are anonymous (position, narrative, outcome): no name, no address, no launch time,
- examples are resolved tokens older than RADAR_EXAMPLE_MIN_AGE_DAYS that passed the Lore Safety Worker.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.db.radar_schema import ensure_radar_schema
from app.services.radar_status import snapshot
from app.services.lore_safety import check_profanity, strip_urls, strip_zero_width_and_bidi
from app.services.radar import (
    CONF_NONE, UNCLUSTERED_ID, RadarParams, confidence_level, narrative_status, wilson_interval,
)

router = APIRouter(prefix="/api")

CACHE_TTL_SECONDS = 3600.0
HISTORY_DAYS = 30
HISTORY_WINDOW_DAYS = 7
MAX_EXAMPLES = 5

_cache: dict[str, tuple[float, Any]] = {}
_schema_ready = False


def invalidate_radar_cache() -> None:
    _cache.clear()


def _cached(key: str) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _store(key: str, value: Any) -> Any:
    _cache[key] = (time.time(), value)
    return value


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.isoformat()


# ------------------------------------------------------------------------------------------ serializers (pure)

def serialize_rate(reached: int, n_resolved: int, rate: Optional[float], params: RadarParams) -> dict:
    """The one place a survival percentage is released: below the minimum sample it is withheld."""
    conf = confidence_level(n_resolved, params)
    if conf == CONF_NONE or rate is None:
        return {"n_resolved": n_resolved, "reached_30k": None, "survival_rate": None, "ci": None,
                "confidence": CONF_NONE, "note": f"Not enough data yet (n = {n_resolved})"}
    lo, hi = wilson_interval(reached, n_resolved)
    return {"n_resolved": n_resolved, "reached_30k": reached, "survival_rate": rate, "ci": [lo, hi],
            "confidence": conf, "note": "low confidence" if conf == "low" else None}


def serialize_cluster(row: dict, baseline: Optional[float], params: RadarParams) -> dict:
    n_resolved = int(row["n_resolved"] or 0)
    rate = _f(row["survival_rate"])
    shown = serialize_rate(int(row["reached_30k"] or 0), n_resolved, rate, params)
    withheld = shown["confidence"] == CONF_NONE
    # Recomputed from the shown rate so the withheld case can never leak through the lift
    lift = None if withheld or rate is None or not baseline else rate / baseline
    saturation = bool(row["saturation"])
    trend = _f(row["trend_pct"])
    return {
        "cluster_id": row["cluster_id"], "label": row["label"], "keywords": list(row["keywords"] or []),
        "label_source": row["label_source"],
        "n_total": int(row["n_total"] or 0), "n_resolved": n_resolved, "reached_30k": shown["reached_30k"],
        "survival_rate": shown["survival_rate"], "ci": shown["ci"], "confidence": shown["confidence"],
        "note": shown["note"], "lift": lift, "median_peak_mc": _f(row["median_peak_mc"]),
        "launches_7d": int(row["launches_7d"] or 0), "trend_pct": trend, "share_7d": _f(row["share_7d"]),
        "survival_7d": None if withheld else _f(row["survival_7d"]),
        "survival_30d": None if withheld else _f(row["survival_30d"]),
        "saturation": saturation, "status": narrative_status(n_resolved, saturation, trend, params),
        "centroid": [_f(row["centroid_x"]), _f(row["centroid_y"])],
    }


def build_radar_payload(run: dict, clusters: list[dict], params: RadarParams) -> dict:
    pj = run.get("params_json") or {}
    baseline = _f(run["baseline_rate"])
    n_res = int(run["n_resolved"] or 0)
    base_ci = wilson_interval(round(baseline * n_res), n_res) if baseline is not None and n_res else (None, None)
    rows = [serialize_cluster(c, baseline, params) for c in clusters if c["cluster_id"] != UNCLUSTERED_ID]
    rows.sort(key=lambda c: (-c["n_resolved"], c["cluster_id"]))  # thin data never tops the list
    un = next((c for c in clusters if c["cluster_id"] == UNCLUSTERED_ID), None)
    unclustered = None
    if un is not None:
        shown = serialize_rate(int(un["reached_30k"] or 0), int(un["n_resolved"] or 0), _f(un["survival_rate"]), params)
        unclustered = {"n_total": int(un["n_total"] or 0), "n_resolved": shown["n_resolved"],
                       "survival_rate": shown["survival_rate"], "ci": shown["ci"], "confidence": shown["confidence"]}
    return {
        "run_id": run["run_id"], "run_at": _iso(run["run_at"]),
        "method": run["method"], "projection": pj.get("projection"),
        "params": {"min_cluster_size": pj.get("min_cluster_size"), "embedder": pj.get("embedder")},
        "thresholds": {"min_resolved": params.min_resolved, "normal_resolved": params.normal_resolved},
        "totals": {"n_tokens": int(run["n_tokens"] or 0), "n_clusters": len(rows),
                   "n_lore_missing": int(pj.get("n_lore_missing") or 0)},
        "baseline": {"survival_rate": baseline, "n_resolved": n_res,
                     "ci": None if base_ci[0] is None else list(base_ci)},
        "unclustered": unclustered, "clusters": rows,
    }


def build_points_payload(run_id: str, point_rows: list[dict]) -> dict:
    """Anonymous points only. Whatever else the rows carry is dropped here."""
    return {
        "run_id": run_id,
        "points": [{
            "x": round(float(r["x"]), 4), "y": round(float(r["y"]), 4), "cluster_id": r["cluster_id"],
            "resolved": bool(r["resolved"]),
            "reached_30k": bool(r["reached_30k"]) if r["resolved"] and r["reached_30k"] is not None else None,
        } for r in point_rows],
    }


def build_history(launches: list[dict], now: datetime, params: RadarParams, *, days: int = HISTORY_DAYS,
                  window_days: int = HISTORY_WINDOW_DAYS) -> list[dict]:
    """
    Per day over the last `days`: launches that day, and the survival rate of resolved tokens launched in the
    trailing `window_days` (with its n). A window under RADAR_MIN_WINDOW_RESOLVED resolved tokens has no rate.
    """
    out = []
    today = now.astimezone(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        launched = sum(1 for r in launches if start <= r["launched_at"] < end)
        win = [r for r in launches if r["resolved"] and end - timedelta(days=window_days) <= r["launched_at"] < end]
        n = len(win)
        rate = None if n < params.min_window_resolved else sum(1 for r in win if r["reached_30k"]) / n
        out.append({"date": day.isoformat(), "launches": launched, "n_resolved_window": n, "survival_window": rate})
    return out


def serialize_example(row: dict) -> Optional[dict]:
    """None when the token must not be shown (profane name or nothing left of the lore after sanitising)."""
    name = strip_zero_width_and_bidi(row.get("name") or "").strip()
    symbol = strip_zero_width_and_bidi(row.get("symbol") or "").strip()
    lore = strip_urls(strip_zero_width_and_bidi(row.get("lore_display") or "")).strip()
    if not name or not lore or check_profanity(name) or check_profanity(symbol) or check_profanity(lore):
        return None
    return {"name": name, "symbol": symbol, "lore": lore,
            "outcome": "reached_30k" if row["status"] == "passed" else "stalled",
            "peak_mc": _f(row["peak_mc"]), "launched_at": _iso(row["launched_at"])}


# ------------------------------------------------------------------------------------------ queries

async def _ready(db: AsyncSession) -> None:
    global _schema_ready
    if not _schema_ready:  # the page works even where the worker is disabled or has not started yet
        await ensure_radar_schema(db)
        _schema_ready = True


async def load_latest_run(db: AsyncSession) -> Optional[dict]:
    await _ready(db)
    row = (await db.execute(text("SELECT * FROM radar_runs ORDER BY run_at DESC LIMIT 1"))).mappings().first()
    return dict(row) if row else None


async def _run_or_404(db: AsyncSession) -> dict:
    try:
        run = await load_latest_run(db)
    except Exception as e:
        print(f"[API ERROR] radar failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="Radar data unavailable")
    if run is None:
        raise HTTPException(status_code=404, detail="Radar has not produced a run yet")
    return run


async def _load_clusters(db: AsyncSession, run_id: str) -> list[dict]:
    res = await db.execute(text(
        "SELECT run_id, cluster_id, label, keywords, label_source, n_total, n_resolved, reached_30k, survival_rate, "
        "ci_low, ci_high, lift, median_peak_mc, launches_7d, trend_pct, share_7d, survival_7d, survival_30d, "
        "saturation, centroid_x, centroid_y FROM radar_clusters WHERE run_id = :r"), {"r": run_id})
    return [dict(r) for r in res.mappings().all()]


# ------------------------------------------------------------------------------------------ routes

@router.get("/radar")
async def get_radar(db: AsyncSession = Depends(get_db)):
    """GET /api/radar - baseline, unclustered bucket and every narrative of the latest run (cache 1h)."""
    hit = _cached("radar")
    if hit is not None:
        return hit
    run = await _run_or_404(db)
    try:
        payload = build_radar_payload(run, await _load_clusters(db, run["run_id"]), RadarParams.from_settings())
    except Exception as e:
        print(f"[API ERROR] radar failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="Radar data unavailable")
    return _store("radar", payload)


@router.get("/radar/status")
async def get_radar_status():
    """GET /api/radar/status - what the worker is doing and which embedders the server has (no data, no secrets)."""
    return snapshot()


@router.get("/radar/points")
async def get_radar_points(db: AsyncSession = Depends(get_db)):
    """GET /api/radar/points - anonymous map points (no token name, no address). Cache 1h."""
    hit = _cached("points")
    if hit is not None:
        return hit
    run = await _run_or_404(db)
    try:
        res = await db.execute(text(
            "SELECT x, y, cluster_id, resolved, reached_30k FROM radar_points WHERE run_id = :r"), {"r": run["run_id"]})
        payload = build_points_payload(run["run_id"], [dict(r) for r in res.mappings().all()])
    except Exception as e:
        print(f"[API ERROR] radar points failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="Radar data unavailable")
    return _store("points", payload)


@router.get("/radar/{cluster_id}")
async def get_radar_cluster(cluster_id: str, db: AsyncSession = Depends(get_db)):
    """GET /api/radar/{cluster_id} - one narrative plus its last 30 days (daily launches, rolling survival)."""
    key = f"detail:{cluster_id}"
    hit = _cached(key)
    if hit is not None:
        return hit
    run = await _run_or_404(db)
    params = RadarParams.from_settings()
    try:
        clusters = [c for c in await _load_clusters(db, run["run_id"]) if c["cluster_id"] == cluster_id]
        if not clusters or cluster_id == UNCLUSTERED_ID:
            raise HTTPException(status_code=404, detail="Unknown narrative")
        res = await db.execute(text(
            "SELECT launched_at, resolved, reached_30k FROM radar_points WHERE run_id = :r AND cluster_id = :c"),
            {"r": run["run_id"], "c": cluster_id})
        launches = [dict(r) for r in res.mappings().all() if r["launched_at"] is not None]
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API ERROR] radar detail failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="Radar data unavailable")
    payload = {
        "run_id": run["run_id"], "run_at": _iso(run["run_at"]),
        "baseline": {"survival_rate": _f(run["baseline_rate"]), "n_resolved": int(run["n_resolved"] or 0)},
        "cluster": serialize_cluster(clusters[0], _f(run["baseline_rate"]), params),
        "history": build_history(launches, datetime.now(timezone.utc), params),
    }
    return _store(key, payload)


@router.get("/radar/{cluster_id}/examples")
async def get_radar_examples(cluster_id: str, db: AsyncSession = Depends(get_db)):
    """GET /api/radar/{cluster_id}/examples - up to 5 resolved tokens older than 7 days that passed lore safety."""
    key = f"examples:{cluster_id}"
    hit = _cached(key)
    if hit is not None:
        return hit
    run = await _run_or_404(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.RADAR_EXAMPLE_MIN_AGE_DAYS)
    try:
        # md5 order: a fixed, outcome-blind pick per run, so examples cannot be cherry-picked winners
        res = await db.execute(text(
            "SELECT t.name, t.symbol, t.lore_display, t.status::text AS status, t.peak_mc, t.launched_at "
            "FROM radar_points p JOIN tokens t ON t.mint = p.token_address "
            "WHERE p.run_id = :r AND p.cluster_id = :c AND t.status::text IN ('passed', 'stalled') "
            "AND t.launched_at < :cutoff AND t.lore_withheld = FALSE AND COALESCE(t.lore_display, '') <> '' "
            "ORDER BY md5(t.mint || :r) LIMIT 40"), {"r": run["run_id"], "c": cluster_id, "cutoff": cutoff})
        examples = [e for e in (serialize_example(dict(r)) for r in res.mappings().all()) if e][:MAX_EXAMPLES]
    except Exception as e:
        print(f"[API ERROR] radar examples failed: {e}", flush=True)
        raise HTTPException(status_code=503, detail="Radar data unavailable")
    return _store(key, {"run_id": run["run_id"], "cluster_id": cluster_id, "examples": examples})
