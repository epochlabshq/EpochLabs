"""
Minimal JSON-RPC client for Robinhood Chain (chain 4663) plus pure log decoders.
httpx only; no web3 dependency. Blockscout is used for links, never as a data source
(its API sits behind a Cloudflare challenge).
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# Launched(address indexed token, address indexed pair, bytes32 indexed commitment, string name,
#          string symbol, uint16 provenFloorBps, uint256 liquidityWei, uint256 lpBurned, address caller)
TOPIC_LAUNCHED = "0xe0998bb984b5ca390315e25fc782c12e3b85849c2041609023bfe22880f940e1"
# AgentUpdated(address indexed previousAgent, address indexed newAgent)
TOPIC_AGENT_UPDATED = "0xee7760cb405d3beb1072ae2e716857daa45a5dd52bc1383a21173199d32c7dee"


class RpcError(Exception):
    pass


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def address_topic(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _words(data: str) -> list[str]:
    raw = data.removeprefix("0x")
    return [raw[i:i + 64] for i in range(0, len(raw), 64)]


def decode_transfer(log: dict) -> dict:
    return {
        "from": topic_to_address(log["topics"][1]),
        "to": topic_to_address(log["topics"][2]),
        "amount_wei": int(log["data"], 16) if log["data"] not in ("0x", "") else 0,
    }


def decode_launched(log: dict) -> dict:
    words = _words(log["data"])
    return {
        "token": topic_to_address(log["topics"][1]),
        "pair": topic_to_address(log["topics"][2]),
        "commitment": log["topics"][3],
        "proven_floor_bps": int(words[2], 16),
        "caller": "0x" + words[5][-40:],
    }


def decode_agent_updated(log: dict) -> dict:
    return {"new_agent": topic_to_address(log["topics"][2])}


def log_position(log: dict) -> tuple[int, int]:
    return int(log["blockNumber"], 16), int(log["logIndex"], 16)


# ---------------------------------------------------------------------------
# RPC client
# ---------------------------------------------------------------------------

class ChainReader:
    def __init__(self, rpc_url: str, client: Optional[httpx.AsyncClient] = None, timeout: float = 20.0):
        import os
        self.rpc_url = rpc_url
        self._sni_host = None
        pin_ip = os.getenv("RPC_PIN_IP", "104.20.46.209")
        if pin_ip and "rpc.mainnet.chain.robinhood.com" in rpc_url:
            self._sni_host = "rpc.mainnet.chain.robinhood.com"
            self.rpc_url = rpc_url.replace("rpc.mainnet.chain.robinhood.com", pin_ip)
            default_headers = {"Host": self._sni_host}
        else:
            default_headers = {}
        self._client = client or httpx.AsyncClient(timeout=timeout, verify=False if self._sni_host else True, headers=default_headers)
        self._owns_client = client is None
        self._ts_cache: dict[int, datetime] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _batch(self, calls: list[tuple[str, list]]) -> list:
        if not calls:
            return []
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        # The public RPC rate-limits (429) and occasionally drops connections: back off and retry
        for attempt in range(6):
            try:
                req = self._client.build_request("POST", self.rpc_url, json=payload)
                if self._sni_host:
                    req.extensions["sni_hostname"] = self._sni_host
                res = await self._client.send(req)
                if res.status_code == 429 or res.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {res.status_code}", request=res.request, response=res)
                # Alchemy reports its per-second limit as HTTP 200 with a 429 error inside the body
                body_probe = res.json() if res.status_code == 200 else None
                items = body_probe if isinstance(body_probe, list) else [body_probe] if body_probe else []
                if any(isinstance(it, dict) and (it.get("error") or {}).get("code") == 429 for it in items):
                    raise httpx.HTTPStatusError("rate limited (429 in body)", request=res.request, response=res)
                break
            except (httpx.HTTPStatusError, httpx.TransportError):
                if attempt == 5:
                    raise
                await asyncio.sleep(min(30, 1.5 * 2 ** attempt))
        res.raise_for_status()
        body = res.json()
        if isinstance(body, dict):  # some nodes answer a failed batch with a single error object
            raise RpcError(body.get("error") or body)
        by_id = {item["id"]: item for item in body}
        out = []
        for i in range(len(calls)):
            item = by_id.get(i)
            if item is None or "error" in item:
                raise RpcError(f"{calls[i][0]} failed: {item.get('error') if item else 'missing response'}")
            out.append(item["result"])
        return out

    async def _call(self, method: str, params: list):
        return (await self._batch([(method, params)]))[0]

    async def chain_id(self) -> int:
        return int(await self._call("eth_chainId", []), 16)

    async def block_number(self) -> int:
        return int(await self._call("eth_blockNumber", []), 16)

    async def eth_balance(self, address: str) -> int:
        return int(await self._call("eth_getBalance", [address, "latest"]), 16)

    async def eth_calls(self, calls: list[tuple[str, str]]) -> list[Optional[str]]:
        """eth_call (to, data) at latest; a call that reverts yields None instead of failing the batch."""
        try:
            return await self._batch([("eth_call", [{"to": to, "data": data}, "latest"]) for to, data in calls])
        except RpcError:
            out = []
            for to, data in calls:
                try:
                    out.append(await self._call("eth_call", [{"to": to, "data": data}, "latest"]))
                except RpcError:
                    out.append(None)
            return out

    async def get_logs(self, from_block: int, to_block: int, *, address: Optional[str] = None,
                       topics: Optional[list] = None) -> list[dict]:
        flt: dict = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address:
            flt["address"] = address
        if topics:
            flt["topics"] = topics
        logs = await self._call("eth_getLogs", [flt])
        return [lg for lg in logs if not lg.get("removed")]

    async def block_timestamps(self, blocks: set[int]) -> dict[int, datetime]:
        # Logs on this chain report blockTimestamp 0x0, so read it from the block header
        missing = sorted(b for b in blocks if b not in self._ts_cache)
        for i in range(0, len(missing), 20):
            chunk = missing[i:i + 20]
            results = await self._batch([("eth_getBlockByNumber", [hex(b), False]) for b in chunk])
            for b, blk in zip(chunk, results):
                if blk is None:
                    raise RpcError(f"block {b} not found")
                self._ts_cache[b] = datetime.fromtimestamp(int(blk["timestamp"], 16), tz=timezone.utc)
        return {b: self._ts_cache[b] for b in blocks}

    async def transactions_with_receipts(self, hashes: list[str]) -> dict[str, tuple[dict, dict]]:
        out: dict[str, tuple[dict, dict]] = {}
        for i in range(0, len(hashes), 25):
            chunk = hashes[i:i + 25]
            calls = [("eth_getTransactionByHash", [h]) for h in chunk] + \
                    [("eth_getTransactionReceipt", [h]) for h in chunk]
            results = await self._batch(calls)
            for j, h in enumerate(chunk):
                tx, receipt = results[j], results[len(chunk) + j]
                if tx is not None and receipt is not None:
                    out[h] = (tx, receipt)
        return out
