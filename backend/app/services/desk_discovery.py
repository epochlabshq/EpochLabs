"""
Finds new Robinhood Chain tokens for the Desk's Watching feed straight from DexScreener, so the feed does not
depend on the ingest worker's pace. A token qualifies when it is a real contract address, launched within the
hold window, has a pair with enough liquidity, and a market cap in the Watching band (at or above the watch
minimum, below the take-profit). Qualifying tokens are inserted into `tokens` as pending (what the ingest worker
does); the Desk cycle then counts their holders, prices and scores them like any other watched token.
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from app.core.config import settings

SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
# DexScreener returns at most 30 pairs per query, so many quote/keyword queries widen the net
QUERIES = (
    "WETH ETH USDC USDT robinhood hood ai agent meme cat dog pepe pump coin token baby moon elon trump doge frog "
    "bull bear gold king queen god dragon monkey ape inu shib sol btc grok gpt bot degen based wojak chad rocket "
    "pixel fun cash money zk lab dao swap finance tesla apple nvidia stock wall street rabbit duck bird fish lion "
    "tiger wolf panda"
).split()
ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def qualifying(pairs: list[dict], now: datetime, *, min_mc: float, max_mc: float, min_liq: float,
               max_age_h: int, excluded: frozenset[str]) -> dict[str, dict]:
    """Best qualifying pair per token. Pure, so the criteria are unit-testable."""
    out: dict[str, dict] = {}
    for p in pairs:
        if p.get("chainId") != "robinhood":
            continue
        base = p.get("baseToken") or {}
        addr = base.get("address") or ""
        created = p.get("pairCreatedAt")
        mc = p.get("marketCap") or p.get("fdv")
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        if not ADDR.match(addr) or addr.lower() in excluded or not created or mc is None or not p.get("priceUsd"):
            continue
        age_h = (now.timestamp() * 1000 - created) / 3_600_000
        if not (0 <= age_h < max_age_h) or not (min_mc <= float(mc) < max_mc) or liq < min_liq:
            continue
        prev = out.get(addr.lower())
        if prev is None or liq > prev["liq_usd"]:
            out[addr.lower()] = {
                "mint": addr, "name": (base.get("name") or addr)[:60], "symbol": (base.get("symbol") or "?")[:20],
                "mc_usd": float(mc), "liq_usd": liq,
                "launched_at": datetime.fromtimestamp(created / 1000, tz=timezone.utc),
                "image_url": (p.get("info") or {}).get("imageUrl"),
            }
    return out


async def discover_tokens(db) -> int:
    """Token searching in Watching disabled: all tokens are hardcoded."""
    return 0
