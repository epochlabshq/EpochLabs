"""
GoForge Registry chain reads (brief 3.3, 4, 6.2). Pure decoders plus a thin I/O class.

Everything that gates a submission or a vote fails closed: if a balance, a wallet age or a fee tx cannot be read, the
answer is "unknown", never a guess in the user's favour.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx
from eth_account import Account
from eth_utils import keccak, to_checksum_address

from app.core.config import settings
from app.services import desk_chain
from app.services.chain_reader import TOPIC_TRANSFER, topic_to_address, address_topic

DEAD_ADDRESS = "0x000000000000000000000000000000000000dead"
SEL_BALANCE_OF = "0x70a08231"
TOPIC_DISTRIBUTED = "0x" + keccak(text="Distributed(uint256,uint256,uint256)").hex()
SEL_DISTRIBUTE = "0x" + keccak(text="distribute(uint256)").hex()[:8]
SEL_CREATE_SPLITTER = "0x" + keccak(text="create(address,bytes32)").hex()[:8]
FEE_TX_MAX_AGE_HOURS = 48
READ_TIMEOUT_S = 12.0


def epc_to_wei(amount_epc: float, decimals: int) -> int:
    """Whole EPC to wei without float error (the fee is a decimal setting)."""
    return int(Decimal(str(amount_epc)) * (Decimal(10) ** decimals))


def wei_to_epc(wei: int, decimals: int) -> float:
    return float(Decimal(wei) / (Decimal(10) ** decimals))


# ---------------------------------------------------------------------------
# Submit fee (3.3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeCheck:
    ok: bool
    amount_wei: int = 0
    reason: Optional[str] = None


def check_fee_receipt(receipt: Optional[dict], *, epc: str, burn: str, sender: str, min_amount_wei: int) -> FeeCheck:
    """
    The tx must have succeeded and emitted EPC Transfer(sender -> burn address) events that add up to at least the
    fee. Only logs from the EPC contract count, so a transfer of any other token to the dead address proves nothing.
    """
    if not receipt:
        return FeeCheck(False, 0, "tx_not_found")
    if str(receipt.get("status")) not in ("0x1", "1"):
        return FeeCheck(False, 0, "tx_failed")
    total = 0
    for lg in receipt.get("logs") or []:
        topics = lg.get("topics") or []
        if (lg.get("address") or "").lower() != epc.lower() or len(topics) < 3 or topics[0].lower() != TOPIC_TRANSFER:
            continue
        if topic_to_address(topics[1]) != sender.lower() or topic_to_address(topics[2]) != burn.lower():
            continue
        data = lg.get("data") or "0x"
        total += int(data, 16) if data not in ("0x", "") else 0
    if total <= 0:
        return FeeCheck(False, 0, "no_epc_burn_from_wallet")
    if total < min_amount_wei:
        return FeeCheck(False, total, "amount_too_low")
    return FeeCheck(True, total, None)


def fee_tx_fresh(block_time: Optional[datetime], now: datetime, max_age_hours: int = FEE_TX_MAX_AGE_HOURS) -> bool:
    """An old burn cannot be recycled as a fee: the tx must be recent."""
    return block_time is not None and 0 <= (now - block_time).total_seconds() <= max_age_hours * 3600


# ---------------------------------------------------------------------------
# Splitter events and calldata (6.2)
# ---------------------------------------------------------------------------

def decode_distributed(log: dict) -> dict:
    data = (log.get("data") or "0x")[2:]
    words = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
    if len(words) < 3:
        raise ValueError("Distributed log has fewer than 3 words")
    return {"creator_wei": words[0], "burn_wei": words[1], "epc_burned_wei": words[2],
            "block": int(log["blockNumber"], 16), "log_index": int(log["logIndex"], 16),
            "tx_hash": log["transactionHash"].lower()}


def distribute_calldata(min_epc_out: int = 0) -> str:
    return SEL_DISTRIBUTE + f"{min_epc_out:064x}"


def create_splitter_calldata(creator: str, idea_id: str) -> str:
    """create(address creator, bytes32 ideaId) with ideaId = keccak256(utf8(idea id)), as scripts/create-splitter.js."""
    return SEL_CREATE_SPLITTER + address_topic(creator)[2:] + keccak(text=idea_id).hex()


def splitter_idea_hash(idea_id: str) -> str:
    return "0x" + keccak(text=idea_id).hex()


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

class GfChain:
    """All network reads and the keeper transaction. Tests substitute a fake with the same methods."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client
        self._first_seen: dict[str, datetime] = {}   # a wallet's first activity never changes: cache successes only

    # --- balances ---------------------------------------------------------

    async def epc_balance(self, wallet: str) -> Optional[float]:
        r = await desk_chain.reader()
        if r is None:
            return None
        try:
            res = await asyncio.wait_for(r.eth_calls([(settings.EPOCH_TOKEN_CA, SEL_BALANCE_OF + address_topic(wallet)[2:])]), READ_TIMEOUT_S)
            word = desk_chain._word(res[0], 0)
            return None if word is None else wei_to_epc(word, settings.GF_EPC_DECIMALS)
        except Exception as e:
            print(f"[GF CHAIN] balance read failed: {type(e).__name__} {e}", flush=True)
            return None

    # --- wallet age -------------------------------------------------------

    async def first_activity(self, wallet: str) -> Optional[datetime]:
        w = wallet.lower()
        if w in self._first_seen:
            return self._first_seen[w]
        found = await self._first_via_alchemy(w) or await self._first_via_blockscout(w)
        if found:
            self._first_seen[w] = found
        return found

    async def _post(self, url: str, payload: dict):
        async def go(c: httpx.AsyncClient):
            res = await c.post(url, json=payload)
            res.raise_for_status()
            return res.json()
        if self._client is not None:
            return await go(self._client)
        async with httpx.AsyncClient(timeout=READ_TIMEOUT_S) as c:
            return await go(c)

    async def _get(self, url: str, params: Optional[dict] = None):
        async def go(c: httpx.AsyncClient):
            res = await c.get(url, params=params)
            res.raise_for_status()
            return res.json()
        if self._client is not None:
            return await go(self._client)
        async with httpx.AsyncClient(timeout=READ_TIMEOUT_S, headers={"accept": "application/json"}) as c:
            return await go(c)

    async def _first_via_alchemy(self, wallet: str) -> Optional[datetime]:
        url = settings.RH_MAINNET_RPC_URL
        if not url:
            return None
        times: list[datetime] = []
        try:
            for key in ("fromAddress", "toAddress"):
                body = await self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers", "params": [{
                    "fromBlock": "0x0", "toBlock": "latest", key: wallet, "category": ["external", "erc20"],
                    "order": "asc", "maxCount": "0x1", "withMetadata": True}]})
                for t in (body.get("result") or {}).get("transfers") or []:
                    ts = (t.get("metadata") or {}).get("blockTimestamp")
                    if ts:
                        times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc))
        except Exception as e:
            print(f"[GF CHAIN] first activity via Alchemy failed: {type(e).__name__} {e}", flush=True)
            return None
        return min(times) if times else None

    async def _first_via_blockscout(self, wallet: str) -> Optional[datetime]:
        """Fallback. Blockscout may ignore the ordering parameters, in which case the oldest of the newest page is taken:
        that makes a wallet look younger than it is, so the error is on the safe side (a vote is refused, not granted)."""
        base = settings.BLOCKSCOUT_BASE.rstrip("/")
        try:
            body = await self._get(f"{base}/api/v2/addresses/{wallet}/transactions", {"sort": "block_number", "order": "asc"})
            stamps = [i.get("timestamp") for i in body.get("items") or [] if i.get("timestamp")]
            if not stamps:
                return None
            return min(datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc) for s in stamps)
        except Exception as e:
            print(f"[GF CHAIN] first activity via Blockscout failed: {type(e).__name__} {e}", flush=True)
            return None

    # --- fee tx -----------------------------------------------------------

    async def fee_tx(self, tx_hash: str) -> Optional[tuple[dict, dict, datetime]]:
        """(tx, receipt, block time) or None when it cannot be read or does not exist."""
        r = await desk_chain.reader()
        if r is None:
            return None
        try:
            pair = (await asyncio.wait_for(r.transactions_with_receipts([tx_hash.lower()]), READ_TIMEOUT_S)).get(tx_hash.lower())
            if not pair:
                return None
            tx, receipt = pair
            block = int(receipt["blockNumber"], 16)
            ts = (await asyncio.wait_for(r.block_timestamps({block}), READ_TIMEOUT_S))[block]
            return tx, receipt, ts
        except Exception as e:
            print(f"[GF CHAIN] fee tx read failed: {type(e).__name__} {e}", flush=True)
            return None

    async def eth_balance(self, address: str) -> Optional[int]:
        r = await desk_chain.reader()
        if r is None:
            return None
        try:
            return await asyncio.wait_for(r.eth_balance(address), READ_TIMEOUT_S)
        except Exception:
            return None

    async def has_code(self, address: str) -> Optional[bool]:
        r = await desk_chain.reader()
        if r is None:
            return None
        try:
            code = await asyncio.wait_for(r._call("eth_getCode", [address.lower(), "latest"]), READ_TIMEOUT_S)
            return code not in (None, "0x")
        except Exception:
            return None

    # --- splitter ---------------------------------------------------------

    async def latest_block(self) -> Optional[int]:
        r = await desk_chain.log_reader() or await desk_chain.reader()
        if r is None:
            return None
        try:
            return await asyncio.wait_for(r.block_number(), READ_TIMEOUT_S)
        except Exception:
            return None

    async def distributions(self, splitter: str, from_block: int, to_block: int) -> list[dict]:
        r = await desk_chain.log_reader() or await desk_chain.reader()
        if r is None:
            return []
        logs = await r.get_logs(from_block, to_block, address=splitter.lower(), topics=[TOPIC_DISTRIBUTED])
        ts = await r.block_timestamps({int(lg["blockNumber"], 16) for lg in logs}) if logs else {}
        out = []
        for lg in logs:
            d = decode_distributed(lg)
            d["at"] = ts[d["block"]]
            out.append(d)
        return out

    async def send_distribute(self, splitter: str, min_epc_out: int = 0) -> Optional[str]:
        """Keeper call. Needs GF_KEEPER_PRIVATE_KEY; returns the tx hash, or None when the keeper is off."""
        key = settings.GF_KEEPER_PRIVATE_KEY
        r = await desk_chain.reader()
        if not key or r is None:
            return None
        acct = Account.from_key(key)
        data = distribute_calldata(min_epc_out)
        nonce = int(await r._call("eth_getTransactionCount", [acct.address, "pending"]), 16)
        gas_price = int(await r._call("eth_gasPrice", []), 16)
        gas = int(await r._call("eth_estimateGas", [{"from": acct.address, "to": splitter, "data": data}]), 16)
        signed = acct.sign_transaction({"to": to_checksum_address(splitter), "value": 0, "data": data, "gas": int(gas * 1.25),
                                        "gasPrice": gas_price, "nonce": nonce, "chainId": settings.CHAIN_ID})
        raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
        return await r._call("eth_sendRawTransaction", ["0x" + bytes(raw).hex()])
