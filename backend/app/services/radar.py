"""
Meta Radar: group every token that cleared $10K by the similarity of its lore, then report how often each
narrative reached $30K. Everything here is pure (arrays and frames in, plain dicts out) so the rules are unit-tested;
the worker does the I/O and the API only serializes.

Honesty rules enforced here, not in the UI:
- survival rate counts resolved tokens only (a pending token only counts towards activity),
- Wilson 95% interval on every rate, sample-size tiers (none / low confidence / normal),
- unclustered tokens stay unclustered, they are never forced into a cluster.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.lore_safety import check_profanity, strip_urls, strip_zero_width_and_bidi

Z95 = 1.959964
UNCLUSTERED_ID = "unclustered"
LABEL_SOURCE_KEYWORDS = "keywords"
UMAP_SEED = 42

CONF_NONE = "insufficient"
CONF_LOW = "low"
CONF_NORMAL = "normal"

STATUS_LOW_DATA = "low_data"
STATUS_SATURATED = "saturated"
STATUS_RISING = "rising"
STATUS_COOLING = "cooling"
STATUS_STEADY = "steady"

_WORD_RE = re.compile(r"(?u)\b[a-zA-Z]{3,}\b")


@dataclass(frozen=True)
class RadarParams:
    min_cluster_size: int = 15
    max_noise_fraction: float = 0.5
    match_max_cosine: float = 0.25
    min_resolved: int = 20
    normal_resolved: int = 50
    min_window_resolved: int = 5
    saturation_min_launches: int = 5
    trend_threshold_pct: float = 25.0
    kmeans_k_range: tuple = (8, 25)

    @classmethod
    def from_settings(cls) -> "RadarParams":
        return cls(
            min_cluster_size=settings.RADAR_MIN_CLUSTER_SIZE, max_noise_fraction=settings.RADAR_MAX_NOISE_FRACTION,
            match_max_cosine=settings.RADAR_MATCH_MAX_COSINE, min_resolved=settings.RADAR_MIN_RESOLVED,
            normal_resolved=settings.RADAR_NORMAL_RESOLVED, min_window_resolved=settings.RADAR_MIN_WINDOW_RESOLVED,
            saturation_min_launches=settings.RADAR_SATURATION_MIN_LAUNCHES,
            trend_threshold_pct=settings.RADAR_TREND_THRESHOLD_PCT,
        )


# ---------------------------------------------------------------------------------------------- statistics

def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a proportion. (None, None) when n == 0."""
    if n <= 0:
        return None, None
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    lo = 0.0 if successes == 0 else max(0.0, centre - half)
    hi = 1.0 if successes == n else min(1.0, centre + half)  # exact bounds: float error must not shave them
    return lo, hi


def confidence_level(n_resolved: int, params: RadarParams) -> str:
    if n_resolved < params.min_resolved:
        return CONF_NONE
    if n_resolved < params.normal_resolved:
        return CONF_LOW
    return CONF_NORMAL


def narrative_status(n_resolved: int, saturation: bool, trend_pct: Optional[float], params: RadarParams) -> str:
    if n_resolved < params.min_resolved:
        return STATUS_LOW_DATA
    if saturation:
        return STATUS_SATURATED
    if trend_pct is not None:
        if trend_pct >= params.trend_threshold_pct:
            return STATUS_RISING
        if trend_pct <= -params.trend_threshold_pct:
            return STATUS_COOLING
    return STATUS_STEADY


# ---------------------------------------------------------------------------------------------- clustering

def l2_normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return x / norms


def _kmeans_silhouette(x: np.ndarray, k_range: tuple) -> Optional[np.ndarray]:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    lo, hi = k_range
    hi = min(hi, len(x) - 1)
    if hi < lo or hi < 2:
        return None
    best_k, best_score = None, -1.0
    sample = min(2000, len(x))
    for k in range(lo, hi + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(x)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(x, labels, sample_size=sample, random_state=42)
        if score > best_score:
            best_k, best_score = k, score
    if best_k is None:
        return None
    return KMeans(n_clusters=best_k, n_init=10, random_state=42).fit_predict(x)


def cluster_embeddings(x: np.ndarray, params: RadarParams) -> tuple[np.ndarray, str]:
    """Labels (-1 = unclustered) and the method used. `x` must already be L2-normalised."""
    from sklearn.cluster import HDBSCAN

    n = len(x)
    if n < max(2, params.min_cluster_size):
        return np.full(n, -1, dtype=int), "hdbscan"
    labels = HDBSCAN(min_cluster_size=params.min_cluster_size, metric="euclidean").fit_predict(x)
    n_clusters = len(set(labels) - {-1})
    noise = float((labels == -1).mean())
    if noise > params.max_noise_fraction or n_clusters < 2:
        km = _kmeans_silhouette(x, params.kmeans_k_range)
        if km is not None:
            return km.astype(int), "kmeans"
    return labels.astype(int), "hdbscan"


def project_2d(x: np.ndarray) -> tuple[np.ndarray, str]:
    """2D map coordinates. UMAP with a fixed seed when installed, otherwise PCA with fixed component signs."""
    n = len(x)
    if n < 4:
        return np.zeros((n, 2)), "none"
    try:
        import umap  # type: ignore
        coords = umap.UMAP(n_neighbors=min(15, n - 1), min_dist=0.1, random_state=UMAP_SEED).fit_transform(x)
        return np.asarray(coords, dtype=float), "umap"
    except Exception:  # umap-learn missing or failing: the map still has to render, and Methodology says which ran
        from sklearn.decomposition import PCA
        pca = PCA(n_components=2, random_state=UMAP_SEED)
        coords = pca.fit_transform(x)
        for i in range(2):
            if pca.components_[i][np.argmax(np.abs(pca.components_[i]))] < 0:
                coords[:, i] *= -1
        return coords, "pca"


def ctfidf_keywords(docs_by_group: dict[int, list[str]], top_n: int = 5, min_df: int = 2) -> dict[int, list[str]]:
    """Class-based TF-IDF keywords (one 'document' per cluster). Profanity, URLs and one-off words are dropped."""
    from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS

    groups = sorted(docs_by_group)
    cleaned = {g: [strip_urls(strip_zero_width_and_bidi(d)) for d in docs_by_group[g] if d] for g in groups}
    all_docs = [d for g in groups for d in cleaned[g]]
    empty = {g: [] for g in groups}
    if not all_docs:
        return empty
    try:
        cv = CountVectorizer(token_pattern=_WORD_RE.pattern, lowercase=True, stop_words=list(ENGLISH_STOP_WORDS),
                             min_df=min(min_df, len(all_docs)))
        cv.fit(all_docs)
    except ValueError:  # empty vocabulary
        return empty
    vocab = np.array(cv.get_feature_names_out())
    if len(vocab) == 0:
        return empty
    counts = np.vstack([cv.transform([" ".join(cleaned[g])]).toarray()[0] for g in groups]).astype(float)
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    tf = counts / row_sums
    avg_words = counts.sum() / max(1, len(groups))
    freq = counts.sum(axis=0)
    freq[freq == 0] = 1.0
    scores = tf * np.log(1 + avg_words / freq)
    out: dict[int, list[str]] = {}
    for i, g in enumerate(groups):
        picked: list[str] = []
        for j in np.argsort(-scores[i], kind="stable"):
            if scores[i][j] <= 0:
                break
            word = str(vocab[j])
            if check_profanity(word):
                continue
            picked.append(word)
            if len(picked) == top_n:
                break
        out[g] = picked
    return out


# ---------------------------------------------------------------------------------------------- stable ids

def format_cluster_id(n: int) -> str:
    return f"c_{n:03d}"


def parse_cluster_id(cid: str) -> Optional[int]:
    m = re.fullmatch(r"c_(\d+)", cid or "")
    return int(m.group(1)) if m else None


def match_clusters(prev: list[tuple[str, np.ndarray]], current: list[np.ndarray], next_id: int,
                   max_cosine: float) -> tuple[list[str], int]:
    """
    Id for every current centroid. Hungarian matching on cosine distance against yesterday's centroids: a match
    within `max_cosine` inherits the old id, everything else gets a fresh one. Returns (ids, next unused number).
    """
    from scipy.optimize import linear_sum_assignment

    ids: list[Optional[str]] = [None] * len(current)
    if prev and current:
        p = l2_normalize(np.vstack([v for _, v in prev]).astype(float))
        c = l2_normalize(np.vstack(current).astype(float))
        cost = 1.0 - c @ p.T
        rows, cols = linear_sum_assignment(cost)
        for r, k in zip(rows, cols):
            if cost[r, k] <= max_cosine:
                ids[r] = prev[k][0]
    for i in range(len(ids)):
        if ids[i] is None:
            ids[i] = format_cluster_id(next_id)
            next_id += 1
    return ids, next_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------- metrics

def _rate(df: pd.DataFrame) -> Optional[float]:
    r = df[df["resolved"]]
    return None if r.empty else float(r["reached"].sum() / len(r))


def _window_rate(df: pd.DataFrame, since: datetime, min_resolved: int) -> Optional[float]:
    w = df[df["resolved"] & (df["launched_at"] > since)]
    return None if len(w) < min_resolved else float(w["reached"].sum() / len(w))


def compute_metrics(group: pd.DataFrame, universe: pd.DataFrame, baseline: Optional[float], now: datetime,
                    params: RadarParams) -> dict:
    """Metrics of one narrative (or of the unclustered bucket). `universe` is every mapped token this run."""
    resolved = group[group["resolved"]]
    n_resolved = int(len(resolved))
    reached = int(resolved["reached"].sum())
    rate = reached / n_resolved if n_resolved else None
    ci_low, ci_high = wilson_interval(reached, n_resolved)
    lift = None if rate is None or not baseline else rate / baseline

    d7, d14, d30 = now - timedelta(days=7), now - timedelta(days=14), now - timedelta(days=30)
    launches_7d = int((group["launched_at"] > d7).sum())
    launches_prev7 = int(((group["launched_at"] > d14) & (group["launched_at"] <= d7)).sum())
    trend_pct = None if launches_prev7 == 0 else (launches_7d - launches_prev7) / launches_prev7 * 100.0
    uni_7d = int((universe["launched_at"] > d7).sum())
    uni_30d = int((universe["launched_at"] > d30).sum())
    share_7d = launches_7d / uni_7d if uni_7d else 0.0
    share_30d = int((group["launched_at"] > d30).sum()) / uni_30d if uni_30d else 0.0
    survival_7d = _window_rate(group, d7, params.min_window_resolved)
    survival_30d = _window_rate(group, d30, params.min_window_resolved)
    saturation = bool(
        launches_7d >= params.saturation_min_launches and share_30d > 0 and share_7d >= 2 * share_30d
        and survival_7d is not None and survival_30d is not None and survival_7d < survival_30d
    )
    return {
        "n_total": int(len(group)), "n_resolved": n_resolved, "reached_30k": reached,
        "survival_rate": rate, "ci_low": ci_low, "ci_high": ci_high, "lift": lift,
        "median_peak_mc": float(resolved["peak_mc"].median()) if n_resolved else None,
        "launches_7d": launches_7d, "trend_pct": trend_pct, "share_7d": share_7d,
        "survival_7d": survival_7d, "survival_30d": survival_30d, "saturation": saturation,
    }


# ---------------------------------------------------------------------------------------------- the run

@dataclass
class PrevCluster:
    cluster_id: str
    centroid_vec: np.ndarray


@dataclass
class RunResult:
    run: dict
    clusters: list[dict] = field(default_factory=list)
    points: list[dict] = field(default_factory=list)
    unclustered: dict = field(default_factory=dict)


def build_run(df: pd.DataFrame, embeddings: np.ndarray, prev: list[PrevCluster], next_id: int, now: datetime,
              params: RadarParams, *, n_lore_missing: int = 0) -> RunResult:
    """
    df columns: mint, lore, lore_withheld, launched_at (tz-aware), peak_mc, status ('passed'|'stalled'|'pending').
    embeddings: one row per df row, same order. Returns what the worker persists.
    """
    if len(df) != len(embeddings):
        raise ValueError("embeddings and tokens must line up row for row")
    df = df.reset_index(drop=True).copy()
    df["resolved"] = df["status"].isin(["passed", "stalled"])
    df["reached"] = df["status"].eq("passed")
    df["peak_mc"] = df["peak_mc"].astype(float)

    x = l2_normalize(np.asarray(embeddings, dtype=float))
    labels, method = cluster_embeddings(x, params) if len(df) else (np.array([], dtype=int), "hdbscan")
    coords, projection = project_2d(x) if len(df) else (np.zeros((0, 2)), "none")

    n_resolved_all = int(df["resolved"].sum())
    baseline = float(df["reached"].sum() / n_resolved_all) if n_resolved_all else None

    group_ids = sorted(set(labels.tolist()) - {-1})
    docs = {g: [str(t) for t, w in zip(df.loc[labels == g, "lore"], df.loc[labels == g, "lore_withheld"])
                if not w and t] for g in group_ids}
    keywords = ctfidf_keywords(docs) if group_ids else {}
    centroids = {g: l2_normalize(x[labels == g].mean(axis=0, keepdims=True))[0] for g in group_ids}
    ids, _ = match_clusters([(p.cluster_id, p.centroid_vec) for p in prev], [centroids[g] for g in group_ids],
                            next_id, params.match_max_cosine)
    cid_of = dict(zip(group_ids, ids))

    clusters = []
    for g in group_ids:
        m = labels == g
        kw = keywords.get(g, [])
        cid = cid_of[g]
        metrics = compute_metrics(df[m], df, baseline, now, params)
        clusters.append({
            "cluster_id": cid, "label": " · ".join(kw[:3]) if kw else f"narrative {cid}", "keywords": kw,
            "label_source": LABEL_SOURCE_KEYWORDS, **metrics,
            "centroid_x": float(coords[m, 0].mean()), "centroid_y": float(coords[m, 1].mean()),
            "centroid_vec": centroids[g].tolist(),
        })
    unclustered = compute_metrics(df[labels == -1], df, baseline, now, params)

    points = [{
        "token_address": df.at[i, "mint"],
        "cluster_id": cid_of[labels[i]] if labels[i] != -1 else UNCLUSTERED_ID,
        "x": float(coords[i, 0]), "y": float(coords[i, 1]),
        "resolved": bool(df.at[i, "resolved"]),
        "reached_30k": bool(df.at[i, "reached"]) if df.at[i, "resolved"] else None,
        "launched_at": df.at[i, "launched_at"],
    } for i in range(len(df))]

    return RunResult(
        run={
            "run_id": f"radar_{now:%Y-%m-%d}", "run_at": now, "n_tokens": int(len(df)), "n_resolved": n_resolved_all,
            "baseline_rate": baseline, "method": method,
            "params_json": {
                "method": method, "projection": projection, "min_cluster_size": params.min_cluster_size,
                "max_noise_fraction": params.max_noise_fraction, "match_max_cosine": params.match_max_cosine,
                "min_resolved": params.min_resolved, "normal_resolved": params.normal_resolved,
                "n_lore_missing": n_lore_missing, "n_clusters": len(clusters),
                "embedder": "all-MiniLM-L6-v2", "umap_seed": UMAP_SEED,
            },
        },
        clusters=clusters, points=points, unclustered=unclustered,
    )
