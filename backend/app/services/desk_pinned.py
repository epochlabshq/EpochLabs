"""
Pinned tokens: a fixed list shown at the bottom of Watching with live DexScreener data (market cap, peak seen,
holders when counted). They are tracked, not scored or traded: most sit far above the take-profit market cap.
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import text

import asyncio

from app.core.config import settings
from app.services.goforge_sources import HEADERS, parse_blockscout_token
from app.services.live_holders import count_holders_full
from app.services.live_market import DEXSCREENER_TOKENS_URL, best_pairs

# (address, symbol, name)
PINNED_TOKENS: list[tuple[str, str, str]] = [
    ("0xab093dEF657F15dF31b33922A95e047aDd645B29", "SHROOM", "Shroom"),
    ("0xdEe52F2ab639b6942B0d0F0565400b93b7a0fbe5", "HARMONIC", "Harmonic Agent"),
    ("0xa92768863a55d8A0591709f7f5E594A249d36Ea3", "ASKR", "Askr"),
    ("0xCdaE63D95D6dd4f89f6e508c77bD4388b4e5C8Ab", "HIVE", "Project Hive"),
    ("0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894", "ZERO", "Zero"),
    ("0x238E40b75Ae78A1388A14e517D855893e92e58db", "SGT", "Sight"),
]

# Pair creation time per pinned token, filled by refresh_pinned (process memory; Age shows "—" until the first read)
_launched: dict[str, datetime] = {}


def launched_at(mint: str):
    return _launched.get(mint.lower())


async def refresh_pinned(db) -> int:
    """Upsert live market data for the pinned tokens into desk_market. Returns how many had a pair."""
    addrs = [a for a, _, _ in PINNED_TOKENS]
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.get(DEXSCREENER_TOKENS_URL + ",".join(addrs))
        res.raise_for_status()
        pairs = res.json() or []
    market = best_pairs(pairs)
    for p in pairs:
        token = ((p.get("baseToken") or {}).get("address") or "").lower()
        created = p.get("pairCreatedAt")
        if token and created:
            at = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            if token not in _launched or at < _launched[token]:
                _launched[token] = at
    now = datetime.now(timezone.utc)
    params = [
        {"mint": addr, "mc": market[addr.lower()]["mc_usd"], "liq": market[addr.lower()]["liq_usd"],
         "price": market[addr.lower()]["price_usd"], "url": market[addr.lower()]["pair_url"],
         "dex": market[addr.lower()]["dex_id"], "at": now}
        for addr in addrs if addr.lower() in market
    ]
    if params:
        await db.execute(text(
            "INSERT INTO desk_market (mint, mc_usd, liq_usd, price_usd, pair_url, dex_id, peak_seen_usd, fetched_at) "
            "VALUES (:mint, :mc, :liq, :price, :url, :dex, :mc, :at) "
            "ON CONFLICT (mint) DO UPDATE SET mc_usd = EXCLUDED.mc_usd, liq_usd = EXCLUDED.liq_usd, "
            "price_usd = EXCLUDED.price_usd, pair_url = EXCLUDED.pair_url, dex_id = EXCLUDED.dex_id, "
            "peak_seen_usd = GREATEST(desk_market.peak_seen_usd, EXCLUDED.peak_seen_usd), fetched_at = EXCLUDED.fetched_at"
        ), params)
        await db.commit()
    return len(params)


async def blockscout_holders(client: httpx.AsyncClient, addr: str) -> Optional[int]:
    """Holder count from Blockscout (one cheap call), or None when it is unreachable or has no count."""
    try:
        res = await client.get(f"{settings.BLOCKSCOUT_BASE.rstrip('/')}/api/v2/tokens/{addr}")
        res.raise_for_status()
        return parse_blockscout_token(res.json()).get("holders")
    except Exception as e:
        print(f"[PINNED] Blockscout holders for {addr[:10]}: {type(e).__name__} {e}", flush=True)
        return None


async def refresh_pinned_holders(db, logs_r, transfers_r, *, max_age_min: int = 30) -> int:
    """
    Count holders onchain for the pinned tokens whose count is missing or older than `max_age_min`, replaying
    each token's whole life from its pair creation. Needs refresh_pinned to have run (it learns the creation time).
    """
    due = (await db.execute(text(
        "SELECT mint FROM desk_live_holders WHERE lower(mint) = ANY(:m) "
        "AND sampled_at > now() - make_interval(mins => :age)"
    ), {"m": [a.lower() for a, _, _ in PINNED_TOKENS], "age": max_age_min})).scalars().all()
    fresh = {m.lower() for m in due}
    todo = [a for a, _, _ in PINNED_TOKENS if a.lower() not in fresh and launched_at(a)]
    if not todo:
        return 0
    sem = asyncio.Semaphore(2)

    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        async def one(addr: str):
            async with sem:
                # Blockscout first (exact and cheap), then the bounded onchain replay
                n = await blockscout_holders(client, addr)
                if n is not None:
                    return addr, (n, 0)
                return addr, await count_holders_full(logs_r, addr, launched_at(addr), transfers_r=transfers_r)

        results = await asyncio.gather(*(one(a) for a in todo))
    now = datetime.now(timezone.utc)
    params = [{"m": a, "h": res[0], "b": res[1], "at": now} for a, res in results if res]
    if params:
        await db.execute(text(
            "INSERT INTO desk_live_holders (mint, holders, block, sampled_at, has_code) "
            "VALUES (:m, :h, :b, :at, true) ON CONFLICT (mint) DO UPDATE SET holders = EXCLUDED.holders, "
            "block = EXCLUDED.block, sampled_at = EXCLUDED.sampled_at, has_code = true"
        ), params)
        await db.commit()
    return len(params)


async def load_pinned_rows(db) -> list[dict]:
    """Pinned tokens in the list order, with whatever market and holder data exists."""
    rows = {r["mint"].lower(): dict(r) for r in (await db.execute(text(
        "SELECT dm.mint, dm.mc_usd, dm.pair_url, dm.fetched_at AS mc_at, dm.peak_seen_usd AS peak_mc, "
        "lh.holders, lh.sampled_at AS holders_sampled_at "
        "FROM desk_market dm LEFT JOIN desk_live_holders lh ON lh.mint = dm.mint WHERE lower(dm.mint) = ANY(:m)"
    ), {"m": [a.lower() for a, _, _ in PINNED_TOKENS]})).mappings().all()}
    out = []
    for addr, symbol, name in PINNED_TOKENS:
        r = rows.get(addr.lower(), {})
        out.append({"mint": addr, "name": name, "symbol": symbol, "mc_now": r.get("mc_usd"),
                    "pair_url": r.get("pair_url"), "mc_at": r.get("mc_at"), "peak_mc": r.get("peak_mc"),
                    "launched_at": launched_at(addr), "holders": r.get("holders"),
                    "holders_sampled_at": r.get("holders_sampled_at")})
    return out
