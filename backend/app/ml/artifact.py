"""
Model artifact: everything needed to score a new token exactly as a training run would.
Stored as canonical JSON (no pickle) so it is inspectable and its sha256 is reproducible.

- booster: LightGBM model string of the classifier fitted on the full labeled set
- pca:     the lore-embedding PCA fitted in that run (mean + components; whiten is never used)
- embedder: the sentence model behind the lore embeddings, or None when they fell back to zeros
"""
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

import numpy as np
from lightgbm import Booster

from app.ml.features import FEATURE_NAMES, embedder_name

ARTIFACT_FORMAT = 1


class FrozenPCA:
    """Drop-in for a fitted sklearn PCA inside extract_features (transform only)."""

    def __init__(self, mean: np.ndarray, components: np.ndarray):
        self.mean_ = mean
        self.components_ = components

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (np.asarray(X, dtype=float) - self.mean_) @ self.components_.T


def build_artifact(clf, pca_model) -> dict:
    if getattr(pca_model, "whiten", False):
        raise ValueError("Whitened PCA is not supported by the artifact format")
    return {
        "format": ARTIFACT_FORMAT,
        "feature_names": list(FEATURE_NAMES),
        "embedder": embedder_name(),
        "pca": {
            "mean": [float(v) for v in pca_model.mean_],
            "components": [[float(v) for v in row] for row in pca_model.components_],
        },
        "booster": clf.booster_.model_to_string(),
    }


def serialize_artifact(artifact: dict) -> tuple[str, str]:
    """Canonical JSON text and its sha256 hex digest."""
    raw = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LiveModel:
    run_id: int
    sha256: str
    feature_names: list[str]
    embedder: Optional[str]
    pca: FrozenPCA
    booster: Booster


def load_artifact(run_id: int, raw: str, expected_sha256: str) -> LiveModel:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"Artifact for run {run_id} fails its sha256 check")
    a = json.loads(raw)
    if a.get("format") != ARTIFACT_FORMAT:
        raise ValueError(f"Unsupported artifact format {a.get('format')} for run {run_id}")
    if a["feature_names"] != FEATURE_NAMES:
        raise ValueError(f"Artifact for run {run_id} was trained on a different feature layout")
    return LiveModel(
        run_id=run_id,
        sha256=digest,
        feature_names=a["feature_names"],
        embedder=a["embedder"],
        pca=FrozenPCA(np.array(a["pca"]["mean"], dtype=float), np.array(a["pca"]["components"], dtype=float)),
        booster=Booster(model_str=a["booster"]),
    )
