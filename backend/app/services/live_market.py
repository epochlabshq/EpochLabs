"""
Live market data for the Desk's Watching feed, from DexScreener (the same source the ingest feed uses).

The ingest worker's own polling is far behind (most pending tokens last polled one to two days ago, and 36 of
the top 50 carry a placeholder peak of exactly $10,500), so the Desk refreshes every watched token's current
market cap itself, once per Desk worker cycle. One request covers 30 tokens.

Nothing here writes to `tokens`: labels stay the job of the ingest/label workers.
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import text

DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/robinhood/"
BATCH = 30


def dexscreener_url(address: str) -> str:
    return f"https://dexscreener.com/robinhood/{address.lower()}"


def best_pairs(pairs: list[dict]) -> dict[str, dict]:
    """The deepest pair per base token: {token: {mc_usd, liq_usd, price_usd, pair_url, dex_id}}."""
    out: dict[str, dict] = {}
    for p in pairs:
        token = ((p.get("baseToken") or {}).get("address") or "").lower()
        if not token:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        if token in out and out[token]["liq_usd"] >= liq:
            continue
        mc = p.get("marketCap") or p.get("fdv")
        out[token] = {
            "mc_usd": float(mc) if mc is not None else None,
            "liq_usd": liq,
            "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
            "pair_url": p.get("url") or dexscreener_url(token),
            "dex_id": p.get("dexId"),
        }
    return out


async def fetch_market(addresses: list[str], client: Optional[httpx.AsyncClient] = None) -> dict[str, dict]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    out: dict[str, dict] = {}
    try:
        for i in range(0, len(addresses), BATCH):
            res = await client.get(DEXSCREENER_TOKENS_URL + ",".join(addresses[i:i + BATCH]))
            res.raise_for_status()
            out.update(best_pairs(res.json() or []))
    finally:
        if own:
            await client.aclose()
    return out


async def refresh_market(db, *, max_age_h: int) -> int:
    """Refresh DexScreener market data for every watched token that exists on Robinhood Chain."""
    mints = [r[0] for r in (await db.execute(text(
        "SELECT t.mint FROM tokens t JOIN desk_live_holders lh ON lh.mint = t.mint AND lh.has_code "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' "
        "AND t.launched_at > now() - make_interval(hours => :h)"
    ), {"h": max_age_h})).all()]
    if not mints:
        return 0
    market = await fetch_market(mints)
    now = datetime.now(timezone.utc)
    for mint in mints:
        m = market.get(mint.lower())
        await db.execute(text(
            "INSERT INTO desk_market (mint, mc_usd, liq_usd, price_usd, pair_url, dex_id, peak_seen_usd, fetched_at) "
            "VALUES (:mint, :mc, :liq, :price, :url, :dex, :mc, :at) "
            "ON CONFLICT (mint) DO UPDATE SET mc_usd = EXCLUDED.mc_usd, liq_usd = EXCLUDED.liq_usd, "
            "price_usd = EXCLUDED.price_usd, pair_url = EXCLUDED.pair_url, dex_id = EXCLUDED.dex_id, "
            "peak_seen_usd = GREATEST(desk_market.peak_seen_usd, EXCLUDED.peak_seen_usd), fetched_at = EXCLUDED.fetched_at"
        ), {"mint": mint, "mc": m["mc_usd"] if m else None, "liq": m["liq_usd"] if m else None,
            "price": m["price_usd"] if m else None, "url": m["pair_url"] if m else None,
            "dex": m["dex_id"] if m else None, "at": now})
    await db.commit()
    return sum(1 for mint in mints if market.get(mint.lower()))
