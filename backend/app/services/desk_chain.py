"""
Live chain reads for The Desk: wallet balance, WETH-pair reserves (mark price) and token decimals.
Read straight from the RPC; a failed read is reported as missing, never filled in.
"""
import asyncio
from typing import Optional

from app.core.config import settings
from app.services.chain_reader import ChainReader, address_topic

SEL_GET_PAIR = "0xe6a43905"      # getPair(address,address)
SEL_GET_RESERVES = "0x0902f1ac"  # getReserves()
SEL_TOKEN0 = "0x0dfe1681"        # token0()
SEL_DECIMALS = "0x313ce567"      # decimals()
ZERO_ADDRESS = "0x" + "0" * 40

READ_TIMEOUT_S = 6.0

_readers: dict[str, Optional[ChainReader]] = {}
# Immutable facts, cached for the life of the process
_pairs: dict[str, Optional[str]] = {}
_token0: dict[str, str] = {}
_decimals: dict[str, Optional[int]] = {}


def _arg(address: str) -> str:
    return address_topic(address)[2:]


def _word(hex_data: Optional[str], i: int) -> Optional[int]:
    if not hex_data or hex_data == "0x":
        return None
    body = hex_data[2:]
    chunk = body[64 * i: 64 * (i + 1)]
    return int(chunk, 16) if len(chunk) == 64 else None


def _address(hex_data: Optional[str]) -> Optional[str]:
    v = _word(hex_data, 0)
    return None if v is None else "0x" + f"{v:040x}"


async def _verified(url: str) -> Optional[ChainReader]:
    """A reader for `url`, only once it is proven to serve CHAIN_ID (checked once per process)."""
    if not url:
        return None
    if url not in _readers:
        r = ChainReader(url)
        try:
            ok = await asyncio.wait_for(r.chain_id(), READ_TIMEOUT_S) == settings.CHAIN_ID
        except Exception as e:
            print(f"[DESK] RPC unreachable ({url.split('/v2/')[0]}): {type(e).__name__} {e}")
            return None  # not cached: retried next time
        if not ok:
            print(f"[DESK] RPC {url.split('/v2/')[0]} does not serve chain {settings.CHAIN_ID}; not used.")
        _readers[url] = r if ok else None
    return _readers[url]


async def reader() -> Optional[ChainReader]:
    """Point reads (balances, eth_call): Alchemy when configured, else the public RPC."""
    return await _verified(settings.RH_MAINNET_RPC_URL) or await _verified(settings.CHAIN_RPC_URL)


async def log_reader() -> Optional[ChainReader]:
    """Log scans over wide block ranges: the public RPC (Alchemy's free tier caps eth_getLogs at 10 blocks)."""
    return await _verified(settings.CHAIN_RPC_URL)


async def transfers_reader() -> Optional[ChainReader]:
    """Alchemy asset transfers (alchemy_getAssetTransfers), when configured."""
    return await _verified(settings.RH_MAINNET_RPC_URL)


async def wallet_balance_wei(r: ChainReader, address: str) -> Optional[int]:
    try:
        return await asyncio.wait_for(r.eth_balance(address), READ_TIMEOUT_S)
    except Exception as e:
        print(f"[DESK] balance read failed: {type(e).__name__} {e}")
        return None


async def _ensure_pairs(r: ChainReader, tokens: list[str]) -> None:
    missing = [t for t in tokens if t not in _pairs]
    if not missing:
        return
    weth = settings.WETH.lower()
    res = await r.eth_calls([(settings.UNISWAP_V2_FACTORY, SEL_GET_PAIR + _arg(t) + _arg(weth)) for t in missing])
    new_pairs = []
    for t, data in zip(missing, res):
        pair = _address(data)
        _pairs[t] = None if pair in (None, ZERO_ADDRESS) else pair
        if _pairs[t]:
            new_pairs.append(_pairs[t])
    if new_pairs:
        res = await r.eth_calls([(p, SEL_TOKEN0) for p in new_pairs])
        for p, data in zip(new_pairs, res):
            t0 = _address(data)
            if t0:
                _token0[p] = t0


async def marks(r: ChainReader, tokens: list[str]) -> dict[str, tuple[int, int]]:
    """{token: (weth_reserve_wei, token_reserve_wei)} for tokens with a WETH pair; others are left out."""
    tokens = [t.lower() for t in tokens]
    if not tokens:
        return {}
    try:
        await asyncio.wait_for(_ensure_pairs(r, tokens), READ_TIMEOUT_S)
        pairs = [(t, _pairs.get(t)) for t in tokens if _pairs.get(t) and _pairs.get(t) in _token0]
        res = await asyncio.wait_for(r.eth_calls([(p, SEL_GET_RESERVES) for _, p in pairs]), READ_TIMEOUT_S)
    except Exception as e:
        print(f"[DESK] reserves read failed: {type(e).__name__} {e}")
        return {}
    out = {}
    for (t, p), data in zip(pairs, res):
        r0, r1 = _word(data, 0), _word(data, 1)
        if r0 is None or r1 is None:
            continue
        out[t] = (r1, r0) if _token0[p] == t else (r0, r1)
    return out


async def decimals(r: Optional[ChainReader], tokens: list[str]) -> dict[str, Optional[int]]:
    tokens = [t.lower() for t in tokens]
    missing = [t for t in tokens if t not in _decimals]
    if missing and r is not None:
        try:
            res = await asyncio.wait_for(r.eth_calls([(t, SEL_DECIMALS) for t in missing]), READ_TIMEOUT_S)
            for t, data in zip(missing, res):
                d = _word(data, 0)
                if d is not None and d <= 36:
                    _decimals[t] = d
        except Exception as e:
            print(f"[DESK] decimals read failed: {type(e).__name__} {e}")
    return {t: _decimals.get(t) for t in tokens}


SEL_TOTAL_SUPPLY = "0x18160ddd"  # totalSupply()
ETH_USD_URL = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
ETH_USD_TTL_S = 60.0
_eth_usd: tuple[float, float] | None = None  # (price, fetched_at)


async def total_supplies(r: ChainReader, tokens: list[str]) -> dict[str, int]:
    """Current totalSupply per token (not cached: supplies can change through burns)."""
    tokens = [t.lower() for t in tokens]
    if not tokens:
        return {}
    try:
        res = await asyncio.wait_for(r.eth_calls([(t, SEL_TOTAL_SUPPLY) for t in tokens]), READ_TIMEOUT_S)
    except Exception as e:
        print(f"[DESK] totalSupply read failed: {type(e).__name__} {e}")
        return {}
    return {t: v for t, data in zip(tokens, res) if (v := _word(data, 0))}


async def eth_usd() -> float | None:
    """ETH/USD from CoinGecko (the same source as the onchain dataset), cached for a minute."""
    global _eth_usd
    import time
    import httpx
    if _eth_usd and time.time() - _eth_usd[1] < ETH_USD_TTL_S:
        return _eth_usd[0]
    try:
        async with httpx.AsyncClient(timeout=READ_TIMEOUT_S) as http:
            price = float((await http.get(ETH_USD_URL)).json()["ethereum"]["usd"])
    except Exception as e:
        print(f"[DESK] ETH/USD unavailable: {type(e).__name__} {e}")
        return None
    if price <= 0:
        return None
    _eth_usd = (price, time.time())
    return price
