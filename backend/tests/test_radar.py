import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.api import radar_endpoints
from app.api.radar_endpoints import (
    build_history, build_points_payload, build_radar_payload, serialize_cluster, serialize_example,
)
from app.db.database import get_db
from app.services.radar import (
    CONF_LOW, CONF_NONE, CONF_NORMAL, STATUS_COOLING, STATUS_LOW_DATA, STATUS_RISING, STATUS_SATURATED,
    STATUS_STEADY, UNCLUSTERED_ID, PrevCluster, RadarParams, build_run, cluster_embeddings, compute_metrics,
    confidence_level, ctfidf_keywords, l2_normalize, match_clusters, narrative_status, wilson_interval,
)
from app.services.radar_worker import last_scheduled, needs_run, seconds_until_next

NOW = datetime(2026, 10, 7, 0, 15, tzinfo=timezone.utc)
P = RadarParams()
DIM = 384


def token_frame(rows):
    """rows: (status, age_days, peak_mc). A frame shaped like what the worker hands to build_run."""
    return pd.DataFrame([{
        "mint": f"0x{i:040x}", "lore": f"lore {i}", "lore_withheld": False,
        "launched_at": NOW - timedelta(days=age, hours=1), "peak_mc": peak, "status": status,
    } for i, (status, age, peak) in enumerate(rows)]).assign(
        resolved=lambda d: d["status"].isin(["passed", "stalled"]), reached=lambda d: d["status"].eq("passed"))


def blob_embeddings(centers, per_blob, sigma, seed):
    rng = np.random.default_rng(seed)
    vecs = [c + rng.normal(0, sigma, size=(per_blob, DIM)) for c in centers]
    return np.vstack(vecs)


def unit_centers(k, seed=7):
    rng = np.random.default_rng(seed)
    return l2_normalize(rng.normal(size=(k, DIM)))


class WilsonTests(unittest.TestCase):
    def test_reference_values(self):
        lo, hi = wilson_interval(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        lo, hi = wilson_interval(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775, places=3)
        lo, hi = wilson_interval(10, 10)
        self.assertAlmostEqual(lo, 0.7225, places=3)
        self.assertEqual(hi, 1.0)
        lo, hi = wilson_interval(20, 100)
        self.assertAlmostEqual(lo, 0.1334, places=3)
        self.assertAlmostEqual(hi, 0.2889, places=3)

    def test_no_sample_no_interval(self):
        self.assertEqual(wilson_interval(0, 0), (None, None))

    def test_interval_contains_rate_and_narrows_with_n(self):
        lo1, hi1 = wilson_interval(3, 10)
        lo2, hi2 = wilson_interval(30, 100)
        self.assertLess(lo1, 0.3)
        self.assertGreater(hi1, 0.3)
        self.assertGreater(lo2, lo1)
        self.assertLess(hi2, hi1)


class SampleSizeTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(confidence_level(0, P), CONF_NONE)
        self.assertEqual(confidence_level(19, P), CONF_NONE)
        self.assertEqual(confidence_level(20, P), CONF_LOW)
        self.assertEqual(confidence_level(49, P), CONF_LOW)
        self.assertEqual(confidence_level(50, P), CONF_NORMAL)

    def test_status(self):
        self.assertEqual(narrative_status(10, True, 500, P), STATUS_LOW_DATA)  # low data beats everything
        self.assertEqual(narrative_status(60, True, 500, P), STATUS_SATURATED)
        self.assertEqual(narrative_status(60, False, 40, P), STATUS_RISING)
        self.assertEqual(narrative_status(60, False, -40, P), STATUS_COOLING)
        self.assertEqual(narrative_status(60, False, 5, P), STATUS_STEADY)
        self.assertEqual(narrative_status(60, False, None, P), STATUS_STEADY)


class MetricTests(unittest.TestCase):
    def test_survival_counts_resolved_only(self):
        rows = [("passed", 10, 40000)] * 3 + [("stalled", 10, 12000)] * 7 + [("pending", 0, 11000)] * 50
        df = token_frame(rows)
        m = compute_metrics(df, df, 0.3, NOW, P)
        self.assertEqual(m["n_total"], 60)
        self.assertEqual(m["n_resolved"], 10)
        self.assertEqual(m["reached_30k"], 3)
        self.assertAlmostEqual(m["survival_rate"], 0.3)
        self.assertEqual(m["median_peak_mc"], 12000.0)
        self.assertEqual(m["launches_7d"], 50)  # pending tokens still count as activity

    def test_lift_is_against_the_baseline(self):
        df = token_frame([("passed", 10, 40000)] * 6 + [("stalled", 10, 1)] * 14)
        self.assertAlmostEqual(compute_metrics(df, df, 0.15, NOW, P)["lift"], 2.0)
        self.assertIsNone(compute_metrics(df, df, None, NOW, P)["lift"])

    def test_trend_and_share(self):
        # 6 launches in the last 7d, 3 in the 7d before; the whole chain launched 12 in the last 7d
        group = token_frame([("pending", 2, 0)] * 6 + [("stalled", 10, 0)] * 3)
        other = token_frame([("pending", 3, 0)] * 6)
        m = compute_metrics(group, pd.concat([group, other], ignore_index=True), 0.2, NOW, P)
        self.assertAlmostEqual(m["trend_pct"], 100.0)
        self.assertAlmostEqual(m["share_7d"], 0.5)

    def test_trend_undefined_without_previous_week(self):
        df = token_frame([("pending", 2, 0)] * 4)
        self.assertIsNone(compute_metrics(df, df, 0.2, NOW, P)["trend_pct"])

    def _saturating(self, recent_survival_wins):
        # Narrative: 8 launches in the last 7d out of 20 chain launches (40%) vs 8 of 80 over 30d (10%)
        older = [("stalled", 20, 1)] * 3 + [("passed", 20, 1)] * 6          # 30d window (days 8..30)
        recent = [("passed", 4, 1)] * recent_survival_wins + [("stalled", 4, 1)] * (8 - recent_survival_wins)
        group = token_frame(older + recent)
        rest = token_frame([("stalled", 25, 1)] * 70 + [("stalled", 5, 1)] * 12)
        return compute_metrics(group, pd.concat([group, rest], ignore_index=True), 0.3, NOW, P)

    def test_saturation_needs_share_jump_and_weaker_recent_survival(self):
        m = self._saturating(recent_survival_wins=1)  # 7d survival 12.5% vs 30d (1+6)/(8+9)=41%
        self.assertTrue(m["saturation"])
        self.assertLess(m["survival_7d"], m["survival_30d"])

    def test_no_saturation_when_recent_survival_holds(self):
        m = self._saturating(recent_survival_wins=8)
        self.assertFalse(m["saturation"])

    def test_no_saturation_on_a_thin_week(self):
        group = token_frame([("stalled", 20, 1)] * 10 + [("stalled", 3, 1)] * 2)
        rest = token_frame([("stalled", 25, 1)] * 100)
        self.assertFalse(compute_metrics(group, pd.concat([group, rest], ignore_index=True), 0.3, NOW, P)["saturation"])


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.centers = unit_centers(3)
        self.params = RadarParams(min_cluster_size=15, kmeans_k_range=(2, 5))

    def _run(self, emb, prev, next_id, seed=0):
        rows = []
        rng = np.random.default_rng(seed)
        for i in range(len(emb)):
            rows.append((str(rng.choice(["passed", "stalled", "pending"], p=[.3, .5, .2])), int(rng.integers(0, 40)), 20000))
        df = token_frame(rows)
        return build_run(df, emb, prev, next_id, NOW, self.params)

    def test_finds_blobs_and_keeps_noise_unclustered(self):
        rng = np.random.default_rng(1)
        emb = np.vstack([blob_embeddings(self.centers, 40, 0.01, 1), l2_normalize(rng.normal(size=(12, DIM)))])
        res = self._run(emb, [], 1)
        self.assertEqual(res.run["method"], "hdbscan")
        self.assertEqual(len(res.clusters), 3)
        self.assertGreaterEqual(res.unclustered["n_total"], 1)  # scattered tokens are not forced into a cluster
        self.assertEqual(sum(c["n_total"] for c in res.clusters) + res.unclustered["n_total"], len(emb))
        un_points = [p for p in res.points if p["cluster_id"] == UNCLUSTERED_ID]
        self.assertEqual(len(un_points), res.unclustered["n_total"])

    def test_cluster_ids_are_stable_between_runs(self):
        emb1 = blob_embeddings(self.centers, 40, 0.01, 1)
        day1 = self._run(emb1, [], 1)
        ids1 = {c["cluster_id"]: np.array(c["centroid_vec"]) for c in day1.clusters}
        self.assertEqual(sorted(ids1), ["c_001", "c_002", "c_003"])

        # Day 2: same narratives with fresh tokens, rows in a different order
        emb2 = blob_embeddings(self.centers, 45, 0.01, 2)
        emb2 = emb2[np.random.default_rng(5).permutation(len(emb2))]
        prev = [PrevCluster(cid, vec) for cid, vec in ids1.items()]
        day2 = self._run(emb2, prev, 4, seed=3)
        self.assertEqual(len(day2.clusters), 3)
        for c in day2.clusters:
            cos = {cid: float(np.dot(c["centroid_vec"], v)) for cid, v in ids1.items()}
            self.assertEqual(c["cluster_id"], max(cos, key=cos.get))  # inherited the id of its nearest old centroid

    def test_new_narrative_gets_a_fresh_id(self):
        day1 = self._run(blob_embeddings(self.centers[:2], 40, 0.01, 1), [], 1)
        prev = [PrevCluster(c["cluster_id"], np.array(c["centroid_vec"])) for c in day1.clusters]
        day2 = self._run(blob_embeddings(self.centers, 40, 0.01, 2), prev, 3)
        self.assertEqual(sorted(c["cluster_id"] for c in day2.clusters), ["c_001", "c_002", "c_003"])

    def test_match_threshold_rejects_distant_centroids(self):
        a, b = unit_centers(2, seed=11)
        ids, nxt = match_clusters([("c_007", a)], [b], 8, 0.25)
        self.assertEqual(ids, ["c_008"])
        self.assertEqual(nxt, 9)
        ids, nxt = match_clusters([("c_007", a)], [a + 0.01 * b], 8, 0.25)
        self.assertEqual(ids, ["c_007"])
        self.assertEqual(nxt, 8)

    def test_one_old_id_is_never_given_twice(self):
        a = unit_centers(1, seed=3)[0]
        ids, _ = match_clusters([("c_001", a)], [a, a + 1e-3], 2, 0.25)
        self.assertEqual(len(set(ids)), 2)

    def test_kmeans_fallback_when_hdbscan_is_mostly_noise(self):
        rng = np.random.default_rng(4)
        x = l2_normalize(np.vstack([blob_embeddings(self.centers, 30, 0.01, 1), rng.normal(size=(40, DIM))]))
        params = RadarParams(min_cluster_size=15, max_noise_fraction=0.0, kmeans_k_range=(2, 5))
        labels, method = cluster_embeddings(x, params)
        self.assertEqual(method, "kmeans")
        self.assertNotIn(-1, labels)

    def test_tiny_universe_is_all_unclustered(self):
        labels, _ = cluster_embeddings(l2_normalize(np.random.default_rng(0).normal(size=(5, DIM))), P)
        self.assertTrue((labels == -1).all())

    def test_baseline_is_resolved_only(self):
        emb = blob_embeddings(self.centers, 40, 0.01, 1)
        df = token_frame([("passed", 10, 1)] * 30 + [("stalled", 10, 1)] * 30 + [("pending", 1, 1)] * 60)
        res = build_run(df, emb, [], 1, NOW, self.params)
        self.assertEqual(res.run["n_tokens"], 120)
        self.assertEqual(res.run["n_resolved"], 60)
        self.assertAlmostEqual(res.run["baseline_rate"], 0.5)

    def test_unresolved_points_carry_no_outcome(self):
        emb = blob_embeddings(self.centers, 40, 0.01, 1)
        df = token_frame([("passed", 10, 1)] * 60 + [("pending", 1, 1)] * 60)
        res = build_run(df, emb, [], 1, NOW, self.params)
        for p in res.points:
            if not p["resolved"]:
                self.assertIsNone(p["reached_30k"])

    def test_mismatched_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            build_run(token_frame([("passed", 1, 1)] * 3), np.zeros((2, DIM)), [], 1, NOW, P)


class KeywordTests(unittest.TestCase):
    def test_ctfidf_picks_distinctive_words(self):
        docs = {
            0: ["ai agent trading bot autonomous", "autonomous ai agent for trading", "trading bot agent ai"],
            1: ["frog meme pepe croak", "pepe the frog meme coin", "croak frog pepe"],
        }
        kw = ctfidf_keywords(docs, top_n=3)
        self.assertTrue({"agent", "trading", "ai"} & set(kw[0]))
        self.assertTrue({"frog", "pepe", "meme"} & set(kw[1]))
        self.assertFalse(set(kw[0]) & set(kw[1]))

    def test_profanity_urls_and_stopwords_are_dropped(self):
        docs = {0: ["retard retard retard https://scam.example/free the the the", "retard whore agent agent"],
                1: ["agent agent other other"]}
        kw = ctfidf_keywords(docs, top_n=5)
        self.assertNotIn("retard", kw[0])
        self.assertNotIn("whore", kw[0])
        self.assertNotIn("the", kw[0])
        self.assertFalse(any("scam" in w or "https" in w for w in kw[0]))

    def test_empty_corpus(self):
        self.assertEqual(ctfidf_keywords({0: [], 1: []}), {0: [], 1: []})

    def test_withheld_lore_never_feeds_labels(self):
        centers = unit_centers(2)
        emb = blob_embeddings(centers, 40, 0.01, 1)
        df = token_frame([("passed", 10, 1)] * 80)
        df["lore"] = ["secretword banana"] * 80
        df.loc[:, "lore_withheld"] = True
        res = build_run(df, emb, [], 1, NOW, RadarParams(kmeans_k_range=(2, 4)))
        for c in res.clusters:
            self.assertEqual(c["keywords"], [])
            self.assertTrue(c["label"].startswith("narrative c_"))


def cluster_row(**kw):
    base = {
        "cluster_id": "c_001", "label": "ai · agent", "keywords": ["ai", "agent"], "label_source": "keywords",
        "n_total": 80, "n_resolved": 60, "reached_30k": 18, "survival_rate": 0.3, "ci_low": 0.2, "ci_high": 0.42,
        "lift": 1.5, "median_peak_mc": 21000, "launches_7d": 9, "trend_pct": 12.0, "share_7d": 0.1,
        "survival_7d": 0.25, "survival_30d": 0.3, "saturation": False, "centroid_x": 1.0, "centroid_y": 2.0,
    }
    base.update(kw)
    return base


class SerializerTests(unittest.TestCase):
    def test_normal_cluster_shows_rate_interval_and_lift(self):
        c = serialize_cluster(cluster_row(), 0.2, P)
        self.assertEqual(c["confidence"], CONF_NORMAL)
        self.assertAlmostEqual(c["survival_rate"], 0.3)
        lo, hi = wilson_interval(18, 60)
        self.assertEqual(c["ci"], [lo, hi])
        self.assertAlmostEqual(c["lift"], 1.5)
        self.assertEqual(c["n_resolved"], 60)

    def test_low_confidence_is_labelled_and_keeps_the_interval(self):
        c = serialize_cluster(cluster_row(n_resolved=30, reached_30k=9), 0.2, P)
        self.assertEqual(c["confidence"], CONF_LOW)
        self.assertEqual(c["note"], "low confidence")
        self.assertIsNotNone(c["ci"])

    def test_under_20_resolved_withholds_every_percentage(self):
        c = serialize_cluster(cluster_row(n_resolved=19, reached_30k=8, survival_rate=8 / 19), 0.2, P)
        self.assertEqual(c["confidence"], CONF_NONE)
        for k in ("survival_rate", "ci", "lift", "reached_30k", "survival_7d", "survival_30d"):
            self.assertIsNone(c[k], k)
        self.assertEqual(c["note"], "Not enough data yet (n = 19)")
        self.assertEqual(c["status"], STATUS_LOW_DATA)
        self.assertEqual(c["n_resolved"], 19)  # the sample size itself is always shown

    def test_payload_orders_by_resolved_not_by_rate_and_shows_baseline(self):
        run = {"run_id": "radar_2026-10-07", "run_at": NOW, "n_tokens": 500, "n_resolved": 400, "baseline_rate": 0.25,
               "method": "hdbscan", "params_json": {"projection": "umap", "n_lore_missing": 7}}
        clusters = [
            cluster_row(cluster_id="c_001", n_resolved=25, reached_30k=20, survival_rate=0.8),   # best rate, thin data
            cluster_row(cluster_id="c_002", n_resolved=200, reached_30k=40, survival_rate=0.2),
            cluster_row(cluster_id=UNCLUSTERED_ID, n_resolved=10, reached_30k=5, survival_rate=0.5),
        ]
        p = build_radar_payload(run, clusters, P)
        self.assertEqual([c["cluster_id"] for c in p["clusters"]], ["c_002", "c_001"])
        self.assertEqual(p["baseline"]["n_resolved"], 400)
        self.assertAlmostEqual(p["baseline"]["survival_rate"], 0.25)
        self.assertEqual(p["totals"], {"n_tokens": 500, "n_clusters": 2, "n_lore_missing": 7})
        self.assertIsNone(p["unclustered"]["survival_rate"])  # 10 resolved: withheld there too
        self.assertAlmostEqual(p["clusters"][0]["lift"], 0.8)
        self.assertEqual(p["projection"], "umap")

    def test_points_are_anonymous(self):
        rows = [{"x": 1.23456, "y": 2.0, "cluster_id": "c_001", "resolved": True, "reached_30k": True,
                 "token_address": "0xdead", "name": "SECRET", "launched_at": NOW},
                {"x": 0, "y": 0, "cluster_id": "c_001", "resolved": False, "reached_30k": True}]
        out = build_points_payload("r", rows)
        self.assertEqual(set(out), {"run_id", "points"})
        for pt in out["points"]:
            self.assertEqual(set(pt), {"x", "y", "cluster_id", "resolved", "reached_30k"})
        self.assertIsNone(out["points"][1]["reached_30k"])  # a pending token has no outcome, whatever the row says
        blob = repr(out)
        self.assertNotIn("0xdead", blob)
        self.assertNotIn("SECRET", blob)

    def test_example_is_dropped_when_unsafe(self):
        ok = {"name": "Frogcoin", "symbol": "FRG", "lore_display": "A frog.", "status": "passed", "peak_mc": 31000,
              "launched_at": NOW - timedelta(days=30)}
        self.assertEqual(serialize_example(ok)["outcome"], "reached_30k")
        self.assertEqual(serialize_example({**ok, "status": "stalled"})["outcome"], "stalled")
        self.assertIsNone(serialize_example({**ok, "name": "retard coin"}))
        self.assertIsNone(serialize_example({**ok, "lore_display": ""}))
        self.assertIsNone(serialize_example({**ok, "lore_display": "https://scam.example"}))
        self.assertNotIn("http", serialize_example({**ok, "lore_display": "buy at https://x.io now"})["lore"])

    def test_history_has_30_days_and_withholds_thin_windows(self):
        launches = [{"launched_at": NOW - timedelta(days=d, hours=2), "resolved": True, "reached_30k": d % 2 == 0}
                    for d in range(3, 12) for _ in range(2)]
        h = build_history(launches, NOW, P)
        self.assertEqual(len(h), 30)
        self.assertEqual(h[-1]["date"], NOW.date().isoformat())
        self.assertEqual(sum(d["launches"] for d in h), len(launches))
        self.assertIsNone(h[0]["survival_window"])  # nothing launched 30 days ago
        self.assertEqual(h[0]["n_resolved_window"], 0)
        busy = [d for d in h if d["n_resolved_window"] >= 5]
        self.assertTrue(busy and all(d["survival_window"] is not None for d in busy))


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    """Answers each query by a keyword in its SQL, like the three tables would."""
    def __init__(self, tables):
        self.tables, self.sql = tables, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append((sql, params))
        for key, rows in self.tables.items():
            if key in sql:
                return FakeResult(rows)
        return FakeResult([])


class EndpointTests(unittest.TestCase):
    RUN = {"run_id": "radar_2026-10-07", "run_at": NOW, "n_tokens": 300, "n_resolved": 200, "baseline_rate": 0.25,
           "method": "hdbscan", "params_json": {"projection": "pca", "n_lore_missing": 3}}

    def setUp(self):
        from app.main import app
        radar_endpoints.invalidate_radar_cache()
        radar_endpoints._schema_ready = True
        self.app = app
        self.db = FakeDb({
            "FROM radar_runs": [self.RUN],
            "FROM radar_clusters": [cluster_row(), cluster_row(cluster_id="c_002", n_resolved=12, reached_30k=9)],
            "FROM radar_points p JOIN tokens": [
                {"name": "Frogcoin", "symbol": "FRG", "lore_display": "A frog.", "status": "passed",
                 "peak_mc": 31000, "launched_at": NOW - timedelta(days=30)},
                {"name": "Badword whore", "symbol": "BAD", "lore_display": "x", "status": "stalled",
                 "peak_mc": 11000, "launched_at": NOW - timedelta(days=30)}],
            "FROM radar_points WHERE run_id = :r AND cluster_id": [
                {"launched_at": NOW - timedelta(days=2), "resolved": False, "reached_30k": None}],
            "FROM radar_points": [{"x": 1, "y": 2, "cluster_id": "c_001", "resolved": True, "reached_30k": False}],
        })

        async def override():
            yield self.db
        app.dependency_overrides[get_db] = override
        self.client = TestClient(app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        radar_endpoints.invalidate_radar_cache()

    def test_radar_endpoint(self):
        r = self.client.get("/api/radar")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([c["cluster_id"] for c in body["clusters"]], ["c_001", "c_002"])
        self.assertIsNone(body["clusters"][1]["survival_rate"])  # n_resolved = 12
        self.assertEqual(body["baseline"]["n_resolved"], 200)

    def test_radar_is_cached(self):
        self.client.get("/api/radar")
        before = len(self.db.sql)
        self.client.get("/api/radar")
        self.assertEqual(len(self.db.sql), before)

    def test_points_endpoint_selects_no_identifying_columns(self):
        r = self.client.get("/api/radar/points")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["points"], [{"x": 1.0, "y": 2.0, "cluster_id": "c_001", "resolved": True,
                                               "reached_30k": False}])
        sql = next(s for s, _ in self.db.sql if "FROM radar_points" in s).lower()
        for banned in ("token_address", "launched_at", "name", "symbol", "mint"):
            self.assertNotIn(banned, sql)

    def test_detail_endpoint(self):
        r = self.client.get("/api/radar/c_001")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["cluster"]["cluster_id"], "c_001")
        self.assertEqual(len(body["history"]), 30)

    def test_unknown_and_unclustered_detail_is_404(self):
        self.assertEqual(self.client.get("/api/radar/c_999").status_code, 404)
        self.assertEqual(self.client.get("/api/radar/unclustered").status_code, 404)

    def test_examples_are_filtered_and_restricted_in_sql(self):
        r = self.client.get("/api/radar/c_001/examples")
        self.assertEqual(r.status_code, 200)
        ex = r.json()["examples"]
        self.assertEqual([e["name"] for e in ex], ["Frogcoin"])  # the profane one is dropped
        for e in ex:
            self.assertNotIn("address", e)
            self.assertNotIn("mint", e)
        sql, params = next((s, p) for s, p in self.db.sql if "JOIN tokens" in s)
        self.assertIn("IN ('passed', 'stalled')", sql)
        self.assertIn("lore_withheld = FALSE", sql)
        self.assertLessEqual(params["cutoff"], datetime.now(timezone.utc) - timedelta(days=7) + timedelta(seconds=5))
        self.assertGreaterEqual(params["cutoff"], datetime.now(timezone.utc) - timedelta(days=7, seconds=30))

    def test_examples_capped_at_five(self):
        self.db.tables["FROM radar_points p JOIN tokens"] = [
            {"name": f"Tok{i}", "symbol": "T", "lore_display": "lore", "status": "passed", "peak_mc": 1,
             "launched_at": NOW - timedelta(days=30)} for i in range(12)]
        self.assertEqual(len(self.client.get("/api/radar/c_001/examples").json()["examples"]), 5)

    def test_no_run_yet_is_a_404_not_an_empty_success(self):
        self.db.tables["FROM radar_runs"] = []
        self.assertEqual(self.client.get("/api/radar").status_code, 404)


class WorkerScheduleTests(unittest.TestCase):
    def test_last_scheduled(self):
        self.assertEqual(last_scheduled(datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc), 0, 15),
                         datetime(2026, 10, 7, 0, 15, tzinfo=timezone.utc))
        self.assertEqual(last_scheduled(datetime(2026, 10, 7, 0, 10, tzinfo=timezone.utc), 0, 15),
                         datetime(2026, 10, 6, 0, 15, tzinfo=timezone.utc))

    def test_runs_once_per_day(self):
        now = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
        self.assertTrue(needs_run(now, None, 0, 15))
        self.assertTrue(needs_run(now, datetime(2026, 10, 6, 0, 16, tzinfo=timezone.utc), 0, 15))
        self.assertFalse(needs_run(now, datetime(2026, 10, 7, 0, 16, tzinfo=timezone.utc), 0, 15))

    def test_sleeps_until_the_next_slot(self):
        now = datetime(2026, 10, 7, 0, 15, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_next(now, 0, 15), 86400)
        self.assertEqual(seconds_until_next(datetime(2026, 10, 7, 23, 15, tzinfo=timezone.utc), 0, 15), 3600)


class EmbedderFallbackTests(unittest.TestCase):
    """Production has no sentence-transformers (it ran the box out of memory): the Radar must still embed."""

    def setUp(self):
        from app.services import radar_worker
        self.w = radar_worker
        self.w._fast_model = None

    def _run(self, st_model, fast_cls):
        import sys
        from unittest import mock
        mods = {"fastembed": mock.Mock(TextEmbedding=fast_cls)} if fast_cls else {"fastembed": None}
        with mock.patch("app.ml.features.get_sentence_model", return_value=st_model), mock.patch.dict(sys.modules, mods):
            return self.w._embed(["a lore", "another lore"])

    def test_uses_fastembed_when_sentence_transformers_is_missing(self):
        class Fake:
            def __init__(self, name):
                self.name = name

            def embed(self, texts, batch_size=64):
                return (np.ones(384) * (i + 1) for i, _ in enumerate(texts))

        emb, label = self._run(None, Fake)
        self.assertEqual(emb.shape, (2, 384))
        self.assertIn("ONNX", label)

    def test_prefers_sentence_transformers_when_installed(self):
        class ST:
            def encode(self, texts, batch_size=64, show_progress_bar=False):
                return np.zeros((len(texts), 384))

        emb, label = self._run(ST(), None)
        self.assertEqual(emb.shape, (2, 384))
        self.assertEqual(label, "all-MiniLM-L6-v2")

    def test_no_embedder_means_no_run(self):
        self.assertIsNone(self._run(None, None))


class CopyRulesTests(unittest.TestCase):
    def test_forbidden_words_are_absent_from_the_page_copy(self):
        root = Path(__file__).resolve().parents[2] / "frontend" / "src"
        files = [root / "config" / "radarCopy.ts", *(root / "components" / "radar").glob("*.tsx"),
                 root / "app" / "radar" / "page.tsx", root / "app" / "radar" / "layout.tsx"]
        existing = [f for f in files if f.exists()]
        self.assertTrue(existing, "radar frontend files not found")
        import re
        banned = re.compile(r"\b(will|best to buy|predicts?|guaranteed)\b", re.IGNORECASE)
        for f in existing:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*", "import ")):
                    continue
                self.assertIsNone(banned.search(line), f"{f.name}:{n}: {stripped}")


if __name__ == "__main__":
    unittest.main()
