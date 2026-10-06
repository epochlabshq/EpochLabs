"""
Dev-only demo: serves the real /api/radar* routes over a synthetic run built by the real build_run pipeline.
No database needed. Run from backend/:  python scripts/radar_demo_server.py   (port 8001)
Point the frontend at it with NEXT_PUBLIC_RADAR_API_BASE_URL=http://localhost:8001
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import uvicorn

from app.api import radar_endpoints
from app.db.database import get_db
from app.main import app
from app.services.radar import RadarParams, build_run, l2_normalize

NOW = datetime.now(timezone.utc)
rng = np.random.default_rng(3)
DIM = 384
VOCAB = {
    "ai": ["ai", "agent", "trading", "bot", "autonomous", "neural"],
    "frog": ["frog", "pepe", "meme", "croak", "pond"],
    "dog": ["dog", "shiba", "woof", "puppy", "bark"],
    "cat": ["cat", "kitty", "meow", "whiskers", "purr"],
    "space": ["space", "rocket", "moon", "mars", "orbit"],
    "food": ["food", "pizza", "burger", "taco", "snack"],
}
SIZES = {"ai": 170, "frog": 110, "dog": 80, "cat": 55, "space": 40, "food": 24}
CENTERS = l2_normalize(rng.normal(size=(len(VOCAB), DIM)))

rows, embs = [], []
for ci, (name, words) in enumerate(VOCAB.items()):
    for i in range(SIZES[name]):
        age = float(rng.uniform(0, 45))
        resolved = age > 2
        # per-narrative base rates so the table has something to show
        p = {"ai": 0.34, "frog": 0.12, "dog": 0.27, "cat": 0.2, "space": 0.4, "food": 0.15}[name]
        status = ("passed" if rng.random() < p else "stalled") if resolved else "pending"
        lore = " ".join(rng.choice(words, size=5)) + " token"
        rows.append({"mint": f"0x{len(rows):040x}", "lore": lore, "lore_withheld": False,
                     "launched_at": NOW - timedelta(days=age), "peak_mc": float(rng.uniform(10000, 60000)),
                     "status": status, "name": f"{name.title()}Coin{i}", "symbol": f"{name[:3].upper()}{i}"})
        embs.append(CENTERS[ci] + rng.normal(0, 0.03, size=DIM))
for i in range(35):  # scattered tokens: stay unclustered
    rows.append({"mint": f"0x{len(rows):040x}", "lore": "misc words", "lore_withheld": False,
                 "launched_at": NOW - timedelta(days=float(rng.uniform(0, 45))), "peak_mc": 15000.0,
                 "status": "stalled", "name": f"Misc{i}", "symbol": "MSC"})
    embs.append(l2_normalize(rng.normal(size=(1, DIM)))[0])

df = pd.DataFrame(rows)
result = build_run(df.drop(columns=["name", "symbol"]), np.vstack(embs), [], 1, NOW, RadarParams(), n_lore_missing=12)
cluster_of = {p["token_address"]: p["cluster_id"] for p in result.points}

RUN = {**result.run}
CLUSTERS = [{**c, "run_id": RUN["run_id"]} for c in result.clusters]
CLUSTERS.append({"run_id": RUN["run_id"], "cluster_id": "unclustered", "label": "Unclustered", "keywords": [],
                 "label_source": "keywords", "centroid_x": None, "centroid_y": None, **result.unclustered})
POINTS = result.points


class Res:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows
    def first(self): return self.rows[0] if self.rows else None


class Db:
    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM radar_runs" in sql: return Res([RUN])
        if "FROM radar_clusters" in sql: return Res(CLUSTERS)
        if "JOIN tokens" in sql:
            ex = [r for r in rows if cluster_of.get(r["mint"]) == params["c"] and r["status"] != "pending"
                  and r["launched_at"] < params["cutoff"]]
            return Res([{"name": r["name"], "symbol": r["symbol"], "lore_display": r["lore"], "status": r["status"],
                         "peak_mc": r["peak_mc"], "launched_at": r["launched_at"]} for r in ex[:40]])
        if "cluster_id = :c" in sql:
            return Res([p for p in POINTS if p["cluster_id"] == params["c"]])
        if "FROM radar_points" in sql: return Res(POINTS)
        return Res([])


async def override():
    yield Db()

app.dependency_overrides[get_db] = override
radar_endpoints._schema_ready = True
print("clusters:", [(c["cluster_id"], c["label"], c["n_resolved"]) for c in result.clusters], flush=True)
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
