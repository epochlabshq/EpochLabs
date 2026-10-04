"""
GoForge data sources, called from the backend worker only (never from the browser: rate limits and CORS).

Blockscout: token metadata, launch tx, holders, top holders, EPC burns, fee inflow.
DexScreener: price, market cap, liquidity, 24h volume, pair.
RPC: fallback for metadata, launch time and holder count when Blockscout fails (it sits behind a Cloudflare
challenge that sometimes blocks servers).

Every method returns None when the source could not be read. Callers keep the last good value and mark it stale;
a failed read is never turned into 0.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.config import settings
from app.services import desk_chain
from app.services.goforge import DEAD_ADDRESS

TIMEOUT_S = 15.0
MAX_PAGES = 5
HEADERS = {"accept": "application/json", "user-agent": "EpochLabs-GoForge/1.0"}

SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"


@dataclass(frozen=True)
class Pair:
    mc_usd: Optional[float]
    liquidity_usd: float
    price_usd: Optional[float]
    volume_24h_usd: Optional[float]
    pair_url: Optional[str]
    name: Optional[str]
    symbol: Optional[str]


@dataclass(frozen=True)
class DexResult:
    """ok False: DexScreener could not be read. ok True and pair None: it answered and there is no pool yet."""
    ok: bool
    pair: Optional[Pair] = None


def _f(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def parse_dex_pairs(pairs, ca: str) -> Optional[Pair]:
    """The deepest pool of `ca` as the base token. A pool with no liquidity does not count as a launch."""
    best, best_liq = None, -1.0
    for p in pairs or []:
        if ((p.get("baseToken") or {}).get("address") or "").lower() != ca.lower():
            continue
        liq = _f((p.get("liquidity") or {}).get("usd")) or 0.0
        if liq > best_liq:
            best, best_liq = p, liq
    if best is None:
        return None
    base = best.get("baseToken") or {}
    return Pair(
        mc_usd=_f(best.get("marketCap")) if _f(best.get("marketCap")) else _f(best.get("fdv")),
        liquidity_usd=best_liq,
        price_usd=_f(best.get("priceUsd")),
        volume_24h_usd=_f((best.get("volume") or {}).get("h24")),
        pair_url=best.get("url"),
        name=base.get("name"),
        symbol=base.get("symbol"),
    )


def parse_blockscout_token(data: dict) -> dict:
    """Normalize /api/v2/tokens/{ca}: Blockscout returns numbers as strings."""
    def to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    holders = data.get("holders_count", data.get("holders"))
    return {
        "name": data.get("name"),
        "symbol": data.get("symbol"),
        "decimals": to_int(data.get("decimals")),
        "total_supply": to_int(data.get("total_supply")),
        "holders": to_int(holders),
    }


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def sum_burn_transfers(items: list[dict], *, sender: str, epc: str, burn: str = DEAD_ADDRESS) -> float:
    """EPC (whole tokens) sent by `sender` to the burn address, from Blockscout token-transfer items."""
    total = 0.0
    for it in items:
        if ((it.get("token") or {}).get("address_hash") or (it.get("token") or {}).get("address") or "").lower() != epc.lower():
            continue
        if ((it.get("from") or {}).get("hash") or "").lower() != sender.lower():
            continue
        if ((it.get("to") or {}).get("hash") or "").lower() != burn.lower():
            continue
        t = it.get("total") or {}
        value, dec = _f(t.get("value")), _f(t.get("decimals"))
        if value is None:
            continue
        total += value / 10 ** int(dec if dec is not None else 18)
    return total


class GoForgeSources:
    """The only place the worker touches the network. Tests substitute a fake with the same methods."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client
        self.blockscout = settings.BLOCKSCOUT_BASE.rstrip("/")

    async def _get(self, url: str, params: Optional[dict] = None):
        async def go(c: httpx.AsyncClient):
            res = await c.get(url, params=params)
            res.raise_for_status()
            return res.json()
        try:
            if self._client is not None:
                return await go(self._client)
            async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=HEADERS) as c:
                return await go(c)
        except Exception as e:
            print(f"[GOFORGE] GET {url.split('?')[0]} failed: {type(e).__name__} {e}", flush=True)
            return None

    # --- DexScreener ------------------------------------------------------

    async def dex(self, ca: str) -> DexResult:
        data = await self._get(f"https://api.dexscreener.com/tokens/v1/{settings.GOFORGE_DEX_CHAIN}/{ca}")
        if data is None or not isinstance(data, list):
            return DexResult(False)
        return DexResult(True, parse_dex_pairs(data, ca))

    # --- Blockscout -------------------------------------------------------

    async def token(self, ca: str) -> Optional[dict]:
        data = await self._get(f"{self.blockscout}/api/v2/tokens/{ca}")
        return parse_blockscout_token(data) if isinstance(data, dict) else None

    async def launch_tx(self, tx_hash: str) -> Optional[dict]:
        """{launched_at, deployer} from Blockscout, else from the RPC (block header timestamp)."""
        data = await self._get(f"{self.blockscout}/api/v2/transactions/{tx_hash}")
        if isinstance(data, dict) and parse_timestamp(data.get("timestamp")):
            return {"launched_at": parse_timestamp(data["timestamp"]),
                    "deployer": ((data.get("from") or {}).get("hash") or "").lower() or None}
        try:
            r = await desk_chain.reader()
            if r is None:
                return None
            tx, _receipt = (await r.transactions_with_receipts([tx_hash]))[tx_hash]
            block = int(tx["blockNumber"], 16)
            ts = (await r.block_timestamps({block}))[block]
            return {"launched_at": ts, "deployer": (tx.get("from") or "").lower() or None}
        except Exception as e:
            print(f"[GOFORGE] launch tx via RPC failed: {type(e).__name__} {e}", flush=True)
            return None

    async def token_via_rpc(self, ca: str) -> Optional[dict]:
        """Metadata fallback: decimals, total supply, name and symbol read from the contract."""
        try:
            r = await desk_chain.reader()
            if r is None:
                return None
            decimals = (await desk_chain.decimals(r, [ca])).get(ca.lower())
            supply = (await desk_chain.total_supplies(r, [ca])).get(ca.lower())
            name, symbol = await r.eth_calls([(ca, SEL_NAME), (ca, SEL_SYMBOL)])
            return {"name": _decode_string(name), "symbol": _decode_string(symbol), "decimals": decimals,
                    "total_supply": supply, "holders": None}
        except Exception as e:
            print(f"[GOFORGE] token via RPC failed: {type(e).__name__} {e}", flush=True)
            return None

    async def holders_via_rpc(self, ca: str, launched_at: datetime) -> Optional[int]:
        """Holder count by scanning Transfer logs: the same method as the Desk's ingest worker."""
        from app.services.live_holders import count_holders
        try:
            res = await count_holders(await desk_chain.log_reader(), ca, launched_at,
                                      transfers_r=await desk_chain.transfers_reader())
            return None if res is None else res[0]
        except Exception as e:
            print(f"[GOFORGE] holders via RPC failed: {type(e).__name__} {e}", flush=True)
            return None

    async def top_holders(self, ca: str) -> Optional[list[dict]]:
        data = await self._get(f"{self.blockscout}/api/v2/tokens/{ca}/holders")
        if not isinstance(data, dict):
            return None
        return [{"value": it.get("value")} for it in data.get("items") or []]

    async def _pages(self, url: str, params: dict) -> Optional[list[dict]]:
        items: list[dict] = []
        page_params: dict = {}
        for _ in range(MAX_PAGES):
            data = await self._get(url, {**params, **page_params})
            if not isinstance(data, dict):
                return None
            items += data.get("items") or []
            page_params = data.get("next_page_params") or {}
            if not page_params:
                return items
        return items

    async def epc_burned(self, fee_router: str) -> Optional[float]:
        """EPC the FeeRouter sent to the dead address (whole tokens), summed over its token transfers."""
        epc = settings.EPOCH_TOKEN_CA
        items = await self._pages(f"{self.blockscout}/api/v2/addresses/{fee_router}/token-transfers",
                                  {"type": "ERC-20", "token": epc})
        return None if items is None else sum_burn_transfers(items, sender=fee_router, epc=epc)

    async def fees_eth(self, fee_router: str) -> Optional[float]:
        """ETH received by the FeeRouter (plain and internal transfers)."""
        total = 0.0
        for path in ("transactions", "internal-transactions"):
            items = await self._pages(f"{self.blockscout}/api/v2/addresses/{fee_router}/{path}", {"filter": "to"})
            if items is None:
                return None
            for it in items:
                if ((it.get("to") or {}).get("hash") or "").lower() != fee_router.lower():
                    continue
                if (it.get("status") or it.get("result")) not in (None, "ok", "success"):
                    continue
                v = _f(it.get("value"))
                total += (v or 0.0) / 1e18
        return total

    async def eth_usd(self) -> Optional[float]:
        return await desk_chain.eth_usd()


def _decode_string(hex_data: Optional[str]) -> Optional[str]:
    """ABI-encoded `string` return value (offset, length, bytes)."""
    if not hex_data or hex_data == "0x":
        return None
    try:
        raw = bytes.fromhex(hex_data[2:])
        if len(raw) == 32:  # bytes32-style name
            return raw.rstrip(b"\0").decode("utf-8", "ignore") or None
        offset = int.from_bytes(raw[:32], "big")
        length = int.from_bytes(raw[offset:offset + 32], "big")
        return raw[offset + 32: offset + 32 + length].decode("utf-8", "ignore") or None
    except Exception:
        return None
