import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier

import app.ml.features as features
from app.ml.artifact import build_artifact, load_artifact, serialize_artifact
from app.ml.features import extract_features
from app.services.scorer import ScorerUnavailable, explain, score


class FakeEmbedder:
    """Deterministic 384-d embedding per text (stands in for MiniLM so tests stay offline)."""

    def encode(self, texts, **_):
        out = []
        for t in texts:
            seed = int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).normal(size=384)
            if "community" in t:
                v[:8] += 3.0
            out.append(v)
        return np.array(out)


def frame(n=240, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    hours = rng.integers(0, 24, n)
    holders = rng.integers(50, 3000, n).astype(float)
    community = rng.random(n) < 0.4
    lore = [("a community coin " if c else "just a coin ") + str(i) for i, c in enumerate(community)]
    logit = 0.002 * (holders - 1500) + 1.2 * community + np.where((hours >= 12) & (hours <= 16), 1.0, -0.5)
    passed = rng.random(n) < 1 / (1 + np.exp(-logit))
    return pd.DataFrame({
        "mint": [f"0x{i:040x}" for i in range(n)],
        "name": [f"Token {i}" for i in range(n)],
        "lore": lore,
        "launched_at": [t0 + timedelta(hours=int(i)) for i in range(n)],
        "launch_hour_utc": hours.astype(float),
        "holders": holders,
        "status": np.where(passed, "passed", "stalled"),
    })


def fit(df):
    X, pca = extract_features(df)
    y = (df["status"] == "passed").astype(int).values
    clf = LGBMClassifier(n_estimators=60, learning_rate=0.1, class_weight="balanced",
                         min_child_samples=10, random_state=42, verbose=-1).fit(X, y)
    return clf, pca, X


class WithFakeEmbedder(unittest.TestCase):
    def setUp(self):
        self._prev = features._sentence_model
        features._sentence_model = FakeEmbedder()
        self.df = frame()
        self.clf, self.pca, self.X = fit(self.df)
        raw, sha = serialize_artifact(build_artifact(self.clf, self.pca))
        self.raw, self.sha = raw, sha
        self.model = load_artifact(9, raw, sha)

    def tearDown(self):
        features._sentence_model = self._prev


class TestArtifact(WithFakeEmbedder):
    def test_round_trip_scores_match_fitted_classifier(self):
        np.testing.assert_allclose(score(self.model, self.df), self.clf.predict_proba(self.X)[:, 1], atol=1e-9)

    def test_new_token_scores_match_fitted_classifier(self):
        new = frame(n=5, seed=99)
        X_new, _ = extract_features(new, pca_model=self.pca)
        np.testing.assert_allclose(score(self.model, new), self.clf.predict_proba(X_new)[:, 1], atol=1e-9)

    def test_serialization_is_canonical(self):
        raw2, sha2 = serialize_artifact(json.loads(self.raw))
        self.assertEqual((raw2, sha2), (self.raw, self.sha))

    def test_tampered_artifact_is_rejected(self):
        tampered = self.raw.replace('"format":1', '"format":1 ')
        with self.assertRaises(ValueError):
            load_artifact(9, tampered, self.sha)

    def test_artifact_records_embedder(self):
        self.assertEqual(self.model.embedder, features.EMBEDDER)
        self.assertEqual(self.model.run_id, 9)


class TestExplain(WithFakeEmbedder):
    def test_top_signals_sorted_and_additive(self):
        rows = self.df.head(10).reset_index(drop=True)
        top_all = explain(self.model, rows, k=5)
        contrib = self.model.booster.predict(extract_features(rows, pca_model=self.model.pca)[0], pred_contrib=True)
        probs = score(self.model, rows)
        for r, signals in enumerate(top_all):
            self.assertEqual(len(signals), 5)
            mags = [abs(s["contribution"]) for s in signals]
            self.assertEqual(mags, sorted(mags, reverse=True))
            for s in signals:
                self.assertEqual(s["effect"], "+" if s["contribution"] > 0 else "-" if s["contribution"] < 0 else "0")
            # Grouped contributions + bias reproduce the model's log-odds
            logit = sum(s["contribution"] for s in signals) + contrib[r, -1]
            self.assertAlmostEqual(1 / (1 + np.exp(-logit)), probs[r], places=3)

    def test_default_k_is_three(self):
        self.assertEqual(len(explain(self.model, self.df.head(1))[0]), 3)

    def test_human_readable_values(self):
        row = pd.DataFrame([{
            "mint": "0x1", "name": "Moon Dog Coin", "lore": "the community runs this one, together forever",
            "launched_at": datetime(2026, 10, 6, 14, 5, tzinfo=timezone.utc),  # a Tuesday
            "launch_hour_utc": 14.0, "holders": 1200.0,
        }])
        values = {s["name"]: s["value"] for s in explain(self.model, row, k=5)[0]}
        self.assertEqual(values, {
            "launch_hour": "14:00 UTC",
            "launch_day": "Tue",
            "holders": "1.2K",
            "lore": '"the community runs this one, tog…"',
            "name": "3 words",
        })

    def test_withheld_and_missing_lore_never_shown(self):
        base = {"mint": "0x1", "name": "X", "launched_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
                "launch_hour_utc": 3.0, "holders": None}
        rows = pd.DataFrame([
            {**base, "lore": "something unsafe", "lore_withheld": True},
            {**base, "lore": None, "lore_withheld": False},
        ])
        for signals, expected in zip(explain(self.model, rows, k=5), ["withheld", "missing"]):
            values = {s["name"]: s["value"] for s in signals}
            self.assertEqual(values["lore"], expected)
            self.assertEqual(values["holders"], "unknown")

    def test_embedder_mismatch_refuses_to_score(self):
        features._sentence_model = None  # this process would fall back to zero embeddings
        with self.assertRaises(ScorerUnavailable):
            score(self.model, self.df.head(1))


class TestTrainerArtifact(unittest.TestCase):
    def test_trainer_returns_artifact_that_scores(self):
        from app.ml.trainer import train_model_and_evaluate
        prev = features._sentence_model
        features._sentence_model = FakeEmbedder()
        try:
            df = frame(n=200, seed=3)
            res = train_model_and_evaluate(df)
            raw, sha = serialize_artifact(res["artifact"])
            probs = score(load_artifact(1, raw, sha), df)
            self.assertEqual(probs.shape, (200,))
            self.assertTrue(((probs >= 0) & (probs <= 1)).all())
        finally:
            features._sentence_model = prev


if __name__ == "__main__":
    unittest.main()
