"""
Build `onchain_tokens`: the demo-trading dataset, derived only from Robinhood Chain logs plus hourly ETH/USD.

It never reads or writes `tokens` (that table holds seeded rows and labels rewritten by the adjust scripts),
and no other script writes `onchain_tokens`. Every row can be re-derived from the blocks it records.

Per WETH pair from the Uniswap V2 factory, newest first, within the ETH/USD history window:
- market cap per Sync: (WETH reserve / token reserve) x totalSupply x ETH/USD; liquidity: 2 x WETH reserve x ETH/USD
- entry: first Sync >= $10K (block + logIndex). `crossed_from_below` is false when the pool opened above $10K
  (large initial liquidity): those never crossed, so they are kept for transparency but excluded from base rate
  and training
- features at the entry log: holders (non-zero balances, excluding the zero address and the pair),
  launch hour/day, minutes from launch to entry, entry market cap and liquidity
- outcome inside 48h from entry: see app.services.onchain_dataset.barrier_outcome

Rows with an open window are refreshed on the next run; closed rows are final and never rewritten.

Usage (from backend/):
  python -m app.db.build_onchain_dataset --days 7 --limit 50 --out dataset.jsonl      # dry run, no DB access
  python -m app.db.build_onchain_dataset --days 88 --write
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.db.backfill_universe import (
    FACTORY, SCAN_WINDOW, SKIP_SYMBOLS, TOPIC_PAIR_CREATED, TOPIC_SYNC, WETH,
    BlockClock, EthUsd, _abi_string, _batched, logs_range,
)
from app.services.chain_reader import ChainReader, TOPIC_TRANSFER, decode_transfer, log_position, topic_to_address
from app.services.onchain_dataset import ZERO, SyncPoint, barrier_outcome, first_entry, holders_at

SOURCE = "onchain_v1"
SYNC_BATCH = 200  # pairs per address-list eth_getLogs
MULTI_SPAN = 100_000  # RPC limit on block span for address-list queries
ANCHOR_STEP = 50_000  # blocks between exact timestamps; interpolation between them is off by seconds

DDL = """
CREATE TABLE IF NOT EXISTS onchain_tokens (
    token             TEXT PRIMARY KEY,
    pair              TEXT NOT NULL,
    name              TEXT,
    symbol            TEXT,
    decimals          SMALLINT NOT NULL,
    supply            DOUBLE PRECISION NOT NULL,
    launch_block      BIGINT NOT NULL,
    launched_at       TIMESTAMPTZ NOT NULL,
    launch_hour_utc   SMALLINT NOT NULL,
    launch_dow        SMALLINT NOT NULL,
    entry_block       BIGINT NOT NULL,
    entry_log_index   INTEGER NOT NULL,
    entry_at          TIMESTAMPTZ NOT NULL,
    minutes_to_entry  DOUBLE PRECISION NOT NULL,
    entry_mc_usd      DOUBLE PRECISION NOT NULL,
    entry_liq_usd     DOUBLE PRECISION NOT NULL,
    entry_eth_usd     DOUBLE PRECISION NOT NULL,
    first_mc_usd      DOUBLE PRECISION NOT NULL,
    crossed_from_below BOOLEAN NOT NULL,
    holders_at_entry  INTEGER NOT NULL,
    horizon_end_at    TIMESTAMPTZ NOT NULL,
    window_closed     BOOLEAN NOT NULL,
    tp_block          BIGINT,
    tp_at             TIMESTAMPTZ,
    sl_block          BIGINT,
    sl_at             TIMESTAMPTZ,
    first_barrier     TEXT CHECK (first_barrier IN ('tp', 'sl', 'timeout')),
    peak_mc_h         DOUBLE PRECISION NOT NULL,
    min_mc_h          DOUBLE PRECISION NOT NULL,
    last_mc_h         DOUBLE PRECISION NOT NULL,
    min_liq_h         DOUBLE PRECISION NOT NULL,
    label_30k_48h     BOOLEAN,
    source            TEXT NOT NULL,
    built_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

OUTCOME_COLS = ["horizon_end_at", "window_closed", "tp_block", "tp_at", "sl_block", "sl_at", "first_barrier",
                "peak_mc_h", "min_mc_h", "last_mc_h", "min_liq_h", "label_30k_48h"]


async def build_clock(reader: ChainReader, lo: int, hi: int) -> BlockClock:
    blocks = set(range(hi, max(0, lo - ANCHOR_STEP), -ANCHOR_STEP)) | {hi, max(0, lo)}
    return BlockClock(await reader.block_timestamps(blocks))


async def multi_address_logs(reader, addrs: list[str], topics: list, frm: int, to: int, parallel: int) -> list[dict]:
    """Logs for many addresses: the RPC caps address-list queries at MULTI_SPAN blocks, so fetch spans in parallel."""
    spans = [(a, min(to, a + MULTI_SPAN - 1)) for a in range(frm, to + 1, MULTI_SPAN)]
    sem = asyncio.Semaphore(parallel)

    async def one(a, b):
        async with sem:
            try:
                return await reader.get_logs(a, b, address=addrs, topics=topics)
            except Exception as e:
                if "limit" in str(e) and b > a:  # too many logs: halve
                    mid = (a + b) // 2
                    return await one(a, mid) + await one(mid + 1, b)
                raise
    return [lg for chunk in await asyncio.gather(*(one(a, b) for a, b in spans)) for lg in chunk]


def sync_points(syncs: list[dict], weth_is_0: bool, decimals: int, supply: float, clock: BlockClock,
                eth: EthUsd) -> list[SyncPoint]:
    out = []
    for s in sorted(syncs, key=log_position):
        d = s["data"][2:]
        r0, r1 = int(d[0:64], 16), int(d[64:128], 16)
        rw, rt = (r0, r1) if weth_is_0 else (r1, r0)
        if rw == 0 or rt == 0:
            continue
        block, idx = log_position(s)
        at = clock.at(block)
        px = eth.at(at)
        out.append(SyncPoint(block, idx, at, (rw / 1e18) / (rt / 10 ** decimals) * supply * px, 2 * rw / 1e18 * px))
    return out


async def build_row(reader, p: dict, meta: list, syncs: list[dict], clock: BlockClock, eth: EthUsd, now: datetime):
    dec_h, sup_h, name_h, sym_h = meta
    if not dec_h or not sup_h or dec_h == "0x" or sup_h == "0x":
        return None
    decimals = int(dec_h, 16)
    if decimals > 36:
        return None
    supply = int(sup_h, 16) / 10 ** decimals
    symbol = _abi_string(sym_h or "0x")[:32]
    if not symbol or symbol.upper() in SKIP_SYMBOLS or supply < 1_000_000:
        return None

    points = sync_points(syncs, p["weth_is_0"], decimals, supply, clock, eth)
    i = first_entry(points)
    if i is None or points[i].at < eth.ts[0]:
        return None
    e = points[i]

    # Exact times for the blocks that matter (launch, entry); recompute the outcome on exact entry time
    exact = await reader.block_timestamps({p["block"], e.block})
    launched_at, entry_at = exact[p["block"]], exact[e.block]
    points[i] = SyncPoint(e.block, e.log_index, entry_at, e.mc_usd, e.liq_usd)
    out = barrier_outcome(points, i, now)
    hits = {b for b in (out["tp_block"], out["sl_block"]) if b is not None}
    if hits:
        ts = await reader.block_timestamps(hits)
        out["tp_at"] = ts.get(out["tp_block"]) if out["tp_block"] else None
        out["sl_at"] = ts.get(out["sl_block"]) if out["sl_block"] else None

    transfers = await logs_range(reader, p["token"], [TOPIC_TRANSFER], max(0, p["block"] - 2000), e.block)
    before = [decode_transfer(t) for t in transfers
              if len(t["topics"]) >= 3 and log_position(t) <= (e.block, e.log_index)]

    return {
        "token": p["token"], "pair": p["pair"], "name": _abi_string(name_h or "0x")[:80] or symbol, "symbol": symbol,
        "decimals": decimals, "supply": supply,
        "launch_block": p["block"], "launched_at": launched_at,
        "launch_hour_utc": launched_at.hour, "launch_dow": launched_at.weekday(),
        "entry_block": e.block, "entry_log_index": e.log_index, "entry_at": entry_at,
        "minutes_to_entry": (entry_at - launched_at).total_seconds() / 60,
        "entry_mc_usd": e.mc_usd, "entry_liq_usd": e.liq_usd, "entry_eth_usd": eth.at(entry_at),
        "first_mc_usd": points[0].mc_usd, "crossed_from_below": i > 0,
        "holders_at_entry": holders_at(before, {ZERO, p["pair"]}),
        **out,
        "source": SOURCE,
    }


async def scan_window(reader, created: list[dict], max_pairs: int, clock: BlockClock, eth: EthUsd, now: datetime,
                      head: int, rate: float, concurrency: int) -> tuple[list[dict], list[dict]]:
    """Pairs created in one factory window (newest first) and the dataset rows they produce."""
    rows: list[dict] = []
    pairs = []
    for lg in sorted(created, key=log_position, reverse=True):
        t0, t1 = topic_to_address(lg["topics"][1]), topic_to_address(lg["topics"][2])
        if WETH not in (t0, t1) or t0 == t1:
            continue
        pairs.append({"pair": "0x" + lg["data"][26:66], "token": t1 if t0 == WETH else t0,
                      "weth_is_0": t0 == WETH, "block": int(lg["blockNumber"], 16)})
    pairs = pairs[:max_pairs]
    if not pairs:
        return [], []
    # Stage 1 (filter): Sync logs for many pairs per query, launch to +50h. Most pairs are dead or never
    # reach $10K. Pairs first reaching $10K more than ~50h after the newest launch in a batch can be missed.
    horizon_blocks = int(50 * 3600 * rate)
    touched: set[str] = set()
    for c in range(0, len(pairs), SYNC_BATCH):
        chunk = pairs[c:c + SYNC_BATCH]
        lo_b = min(q["block"] for q in chunk)
        hi_b = min(head, max(q["block"] for q in chunk) + horizon_blocks)
        for lg in await multi_address_logs(reader, [q["pair"] for q in chunk], [TOPIC_SYNC], lo_b, hi_b, concurrency):
            touched.add(lg["address"].lower())
    # Stage 2: full history only for pairs with any swap; build_row drops those never at $10K
    syncs: dict[str, list[dict]] = {}
    candidates = [p for p in pairs if p["pair"] in touched]
    sem = asyncio.Semaphore(concurrency)

    async def full(p):
        async with sem:
            syncs[p["pair"]] = await logs_range(reader, p["pair"], [TOPIC_SYNC], p["block"], head)
    await asyncio.gather(*(full(p) for p in candidates))
    live = [p for p in pairs if syncs.get(p["pair"])]
    calls = [("eth_call", [{"to": p["token"], "data": sel}, "latest"])
             for p in live for sel in ("0x313ce567", "0x18160ddd", "0x06fdde03", "0x95d89b41")]
    try:
        meta = await _batched(reader, calls, 20)
    except Exception:
        meta = []
        for c in calls:  # one bad token reverts a whole batch: retry singly
            try:
                meta.append(await reader._call(*c))
            except Exception:
                meta.append(None)
    for c in range(0, len(live), concurrency):
        built = await asyncio.gather(*(
            build_row(reader, p, meta[4 * k:4 * k + 4], syncs[p["pair"]], clock, eth, now)
            for k, p in enumerate(live[c:c + concurrency], start=c)))
        rows += [r for r in built if r]
    return pairs, rows


async def main(days: int, skip_hours: float, limit: int, concurrency: int, out_path: str | None, write: bool):
    reader = ChainReader(settings.CHAIN_RPC_URL, timeout=60)
    assert await reader.chain_id() == settings.CHAIN_ID, "RPC is not Robinhood Chain"
    head = await reader.block_number()
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=30) as http:
        cg = (await http.get("https://api.coingecko.com/api/v3/coins/ethereum/market_chart",
                             params={"vs_currency": "usd", "days": 90})).json()
    eth = EthUsd(cg["prices"])
    start = max(now - timedelta(days=days), eth.ts[0])

    # Locate the first block of the window from the chain's own block rate
    probe = await reader.block_timestamps({head, max(0, head - 1_000_000)})
    rate = 1_000_000 / (probe[head] - probe[max(0, head - 1_000_000)]).total_seconds()
    lo = max(0, head - int((now - start).total_seconds() * rate * 1.05))
    clock = await build_clock(reader, lo, head)
    print(f"[ONCHAIN] head {head}, window from {start:%Y-%m-%d %H:%M} (block ~{lo}), {len(clock.blocks)} anchors")

    out = open(out_path, "w", encoding="utf-8") if out_path else None
    rows, scanned, window_end = [], 0, head - int(skip_hours * 3600 * rate)
    while window_end > lo and scanned < limit:
        window_start = max(lo, window_end - SCAN_WINDOW + 1)
        for attempt in range(4):  # the public RPC drops long runs now and then: retry the whole window
            try:
                created = await reader.get_logs(window_start, window_end, address=FACTORY, topics=[TOPIC_PAIR_CREATED])
                pairs, built = await scan_window(reader, created, limit - scanned, clock, eth, now, head, rate, concurrency)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"[ONCHAIN] window {window_start}-{window_end} failed ({type(e).__name__}: {e}); retrying in 60s")
                await asyncio.sleep(60)
        window_end = window_start - 1
        if not pairs:
            continue
        scanned += len(pairs)
        rows += built
        if out:  # written per window so a crash keeps everything built so far
            for r in built:
                out.write(json.dumps(r, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)) + "\n")
            out.flush()
        print(f"[ONCHAIN] scanned {scanned} pairs (back to {clock.at(pairs[-1]['block']):%Y-%m-%d %H:%M}), "
              f"{len(rows)} at or above $10K so far")
    await reader.aclose()
    if out:
        out.close()
        print(f"[ONCHAIN] wrote {len(rows)} rows to {out_path}")

    crossed = [r for r in rows if r["crossed_from_below"]]
    closed = [r for r in crossed if r["window_closed"]]
    hit = sum(1 for r in closed if r["label_30k_48h"])
    print(f"[ONCHAIN] scanned {scanned} pairs: {len(rows)} at or above $10K, {len(rows) - len(crossed)} opened above it; "
          f"{len(crossed)} crossed from below, {len(closed)} with a closed 48h window, "
          f"{hit} reached $30K" + (f" (base rate {hit / len(closed):.1%})" if closed else ""))

    if not write:
        print("[ONCHAIN] no --write: database untouched")
        return

    from app.db.database import AsyncSessionLocal, engine
    cols = list(rows[0]) if rows else []
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in OUTCOME_COLS + ["built_at"])
    async with AsyncSessionLocal() as db:
        await db.execute(text(DDL))
        for r in rows:
            await db.execute(text(
                f"INSERT INTO onchain_tokens ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                f"ON CONFLICT (token) DO UPDATE SET {updates} WHERE onchain_tokens.window_closed = false"
            ), r)
        await db.commit()
        total = (await db.execute(text("SELECT count(*) FROM onchain_tokens"))).scalar()
    await engine.dispose()
    print(f"[ONCHAIN] upserted {len(rows)} rows; onchain_tokens now has {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=88, help="look back this many days (capped by ETH/USD history)")
    ap.add_argument("--skip-hours", type=float, default=0, help="start scanning pairs created this long ago")
    ap.add_argument("--limit", type=int, default=100_000, help="max pairs to scan")
    ap.add_argument("--concurrency", type=int, default=8, help="pairs fetched in parallel")
    ap.add_argument("--out", help="also write rows as JSON lines to this file")
    ap.add_argument("--write", action="store_true", help="upsert into onchain_tokens (otherwise no DB access)")
    a = ap.parse_args()
    asyncio.run(main(a.days, a.skip_hours, a.limit, a.concurrency, a.out, a.write))
