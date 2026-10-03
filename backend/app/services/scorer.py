"""
Live scoring with the latest model run. Pure functions over a token frame plus a thin DB loader.

survival = the run's classifier probability for "passed" (same pipeline as training, via the stored artifact).
top_signals = the largest per-token contributions (LightGBM pred_contrib, log-odds), with related feature
columns summed into one human-readable signal: launch hour, launch day, holders, lore, name.

Holders: a watched token has no 48h sample yet, so it is scored with its current onchain holder count
(desk_live_holders). This is a known mismatch with training, which uses the 48h sample: a younger token
naturally has fewer holders. A token with no count at all is not scored: in the labeled set a missing count
only occurs on tokens that passed, so the model reads "no holders" as "passed" and would score it near 1.0.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.api.endpoints import get_latest_model
from app.ml.artifact import LiveModel, load_artifact
from app.ml.features import embedder_name, extract_features

SIGNAL_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("launch_hour", ("hour_sin", "hour_cos")),
    ("launch_day", tuple(f"dow_{i}" for i in range(7))),
    ("holders", ("holders_log",)),
    ("lore", ("lore_len", "lore_missing") + tuple(f"lore_pca_{i+1}" for i in range(24))),
    ("name", ("name_tokens",)),
]
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_LORE_PREVIEW_CHARS = 32


class ScorerUnavailable(RuntimeError):
    pass


def _features(model: LiveModel, df: pd.DataFrame) -> np.ndarray:
    if model.embedder != embedder_name():
        raise ScorerUnavailable(
            f"Run {model.run_id} was trained with lore embedder {model.embedder!r}, "
            f"this process has {embedder_name()!r}; scores would not match the run."
        )
    X, _ = extract_features(df, pca_model=model.pca)
    return X


def score(model: LiveModel, df: pd.DataFrame, X: Optional[np.ndarray] = None) -> np.ndarray:
    """Survival probability per row of `df` (columns as in the training frame)."""
    return model.booster.predict(_features(model, df) if X is None else X)


def _format_holders(n) -> str:
    if n is None or pd.isna(n):
        return "unknown"
    n = float(n)
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(int(n))


def _signal_value(group: str, row: pd.Series) -> str:
    if group == "launch_hour":
        h = row.get("launch_hour_utc")
        return "unknown" if h is None or pd.isna(h) else f"{int(h):02d}:00 UTC"
    if group == "launch_day":
        at = row.get("launched_at")
        return "unknown" if at is None or pd.isna(at) else _DAYS[pd.Timestamp(at).dayofweek]
    if group == "holders":
        return _format_holders(row.get("holders"))
    if group == "lore":
        if row.get("lore_withheld"):
            return "withheld"
        shown = row.get("lore_display") or row.get("lore")
        if shown is None or (isinstance(shown, float) and pd.isna(shown)) or not str(shown).strip():
            return "missing"
        shown = " ".join(str(shown).split())
        return f'"{shown[:_LORE_PREVIEW_CHARS]}…"' if len(shown) > _LORE_PREVIEW_CHARS else f'"{shown}"'
    if group == "name":
        words = len(str(row.get("name") or "").split())
        return f"{words} word" + ("" if words == 1 else "s")
    raise ValueError(group)


def explain(model: LiveModel, df: pd.DataFrame, k: int = 3, X: Optional[np.ndarray] = None) -> list[list[dict]]:
    """Top-k signals per row, largest absolute contribution first."""
    X = _features(model, df) if X is None else X
    contrib = model.booster.predict(X, pred_contrib=True)  # (n, n_features + 1 bias), log-odds
    index = {name: i for i, name in enumerate(model.feature_names)}
    out = []
    for r, (_, row) in enumerate(df.iterrows()):
        signals = []
        for group, cols in SIGNAL_GROUPS:
            c = float(sum(contrib[r, index[col]] for col in cols))
            signals.append({
                "name": group,
                "value": _signal_value(group, row),
                "effect": "+" if c > 0 else "-" if c < 0 else "0",
                "contribution": round(c, 4),
            })
        signals.sort(key=lambda s: abs(s["contribution"]), reverse=True)
        out.append(signals[:k])
    return out


# ---- DB access -------------------------------------------------------------------------------------------

_cache: dict[int, LiveModel] = {}


async def load_live_model(db) -> Optional[LiveModel]:
    """
    The artifact of the latest model run, i.e. the run whose numbers are published. None when that run has
    no artifact (older runs, or a run trained before artifacts existed): never fall back to an older model.
    """
    latest = await get_latest_model(db)
    if latest is None:
        return None
    if latest.id in _cache:
        return _cache[latest.id]
    row = (await db.execute(
        text("SELECT sha256, artifact FROM model_artifacts WHERE run_id = :id"), {"id": latest.id}
    )).first()
    if row is None:
        return None
    model = await asyncio.to_thread(load_artifact, latest.id, row[1], row[0])
    _cache.clear()
    _cache[latest.id] = model
    return model


async def _load_token_frame(db, mints: list[str]) -> pd.DataFrame:
    res = await db.execute(text(
        "SELECT t.mint, t.name, t.lore, t.lore_display, t.lore_withheld, t.launched_at, t.launch_hour_utc, "
        "COALESCE(t.holders, h.holders) AS holders "
        "FROM tokens t LEFT JOIN desk_live_holders h ON h.mint = t.mint WHERE t.mint = ANY(:mints)"
    ), {"mints": mints})
    df = pd.DataFrame(res.mappings().all())
    if df.empty:
        return df
    # Same dtypes as the training frame (model_worker._load_labeled_frame)
    df["launch_hour_utc"] = df["launch_hour_utc"].astype(float)
    df["holders"] = df["holders"].astype(float)
    return df.reset_index(drop=True)


async def score_mints(db, mints: list[str], k: int = 3) -> dict[str, dict]:
    """{mint: {"survival", "top_signals", "run_id", "scored_at"}} for the given tokens with the live model."""
    model = await load_live_model(db)
    if model is None:
        raise ScorerUnavailable("Latest model run has no stored artifact yet")
    df = await _load_token_frame(db, mints)
    if df.empty:
        return {}
    df = df[df["holders"].notna()].reset_index(drop=True)
    if df.empty:
        return {}

    def _run():
        X = _features(model, df)  # lore embeddings are the expensive part: compute once
        return score(model, df, X), explain(model, df, k, X)

    probs, signals = await asyncio.to_thread(_run)
    now = datetime.now(timezone.utc).isoformat()
    return {
        mint: {"survival": round(float(p), 4), "top_signals": s, "run_id": model.run_id, "scored_at": now}
        for mint, p, s in zip(df["mint"], probs, signals)
    }
