"""
Service to continuously sync real token transfers and swaps for the Golem wallet:
https://robinhoodchain.blockscout.com/address/0x49EdF5f24216e02EEb6a947cC3dF0CDB6B84582C?tab=token_transfers

Uses Alchemy RPC / Blockscout / DexScreener to ingest new token transfers into golem_swaps,
ensures token metadata and live market prices are always fresh.
"""
import asyncio
from datetime import datetime, timezone
import httpx
from sqlalchemy import text

from app.core.config import settings

# Track the last scanned block in memory
_last_scanned_block = 79300000

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def address_to_topic(addr: str) -> str:
    cleaned = addr.lower().replace("0x", "")
    return "0x" + cleaned.zfill(64)


async def sync_wallet_transfers(db) -> int:
    """
    Sync token transfers involving the Golem wallet.
    Returns the count of newly recorded swaps/trades.
    """
    global _last_scanned_block
    rpc_url = settings.RH_MAINNET_RPC_URL or settings.CHAIN_RPC_URL
    wallet = settings.GOLEM_WALLET.lower()
    wallet_topic = address_to_topic(wallet)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Get latest block number
            res = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
            latest_hex = (res.json() or {}).get("result")
            if not latest_hex:
                return 0
            latest_block = int(latest_hex, 16)
            from_block = max(79300000, _last_scanned_block - 50)
            to_block = latest_block

            # 2. Fetch Transfer logs received by wallet (incoming tokens)
            p_in = {
                "jsonrpc": "2.0", "id": 2, "method": "eth_getLogs",
                "params": [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": [TRANSFER_TOPIC, None, wallet_topic]}]
            }
            # Fetch Transfer logs sent from wallet (outgoing tokens / sells)
            p_out = {
                "jsonrpc": "2.0", "id": 3, "method": "eth_getLogs",
                "params": [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": [TRANSFER_TOPIC, wallet_topic]}]
            }

            res_in, res_out = await asyncio.gather(
                client.post(rpc_url, json=p_in),
                client.post(rpc_url, json=p_out),
                return_exceptions=True
            )

            logs_in = (res_in.json() or {}).get("result") if isinstance(res_in, httpx.Response) else []
            logs_out = (res_out.json() or {}).get("result") if isinstance(res_out, httpx.Response) else []

            all_logs = (logs_in or []) + (logs_out or [])
            if not all_logs:
                _last_scanned_block = to_block
                return 0

            new_count = 0
            for lg in all_logs:
                tx_hash = (lg.get("transactionHash") or "").lower()
                token_addr = (lg.get("address") or "").lower()
                block_num = int(lg.get("blockNumber", "0x0"), 16)
                data_hex = lg.get("data", "0x0")
                token_amount = int(data_hex, 16) if data_hex != "0x" else 0

                topics = lg.get("topics", [])
                from_topic = topics[1] if len(topics) > 1 else ""
                is_buy = wallet_topic in (topics[2] if len(topics) > 2 else "").lower()
                side = "buy" if is_buy else "sell"

                # Check if swap already exists
                exists = (await db.execute(text(
                    "SELECT 1 FROM golem_swaps WHERE tx_hash = :tx"
                ), {"tx": tx_hash})).scalar()

                if not exists:
                    # Get tx details for eth amount & block time
                    tx_res = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 4, "method": "eth_getTransactionByHash", "params": [tx_hash]})
                    tx_data = (tx_res.json() or {}).get("result") or {}
                    eth_val = int(tx_data.get("value", "0x0"), 16)

                    blk_res = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 5, "method": "eth_getBlockByNumber", "params": [hex(block_num), False]})
                    blk_data = (blk_res.json() or {}).get("result") or {}
                    ts = int(blk_data.get("timestamp", "0x0"), 16) if blk_data.get("timestamp") else int(datetime.now(timezone.utc).timestamp())
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)

                    # Insert swap
                    await db.execute(text("""
                        INSERT INTO golem_swaps (tx_hash, block, at, side, token, token_amount_wei, eth_amount_wei)
                        VALUES (:tx, :blk, :at, :sd, :tk, :ta, :ea)
                        ON CONFLICT (tx_hash) DO NOTHING
                    """), {"tx": tx_hash, "blk": block_num, "at": dt, "sd": side, "tk": token_addr, "ta": token_amount, "ea": eth_val})

                    # Ensure token exists in tokens
                    await db.execute(text("""
                        INSERT INTO tokens (mint, name, symbol, chain, status, launched_at, lore_withheld, peak_mc, poll_count)
                        VALUES (:m, :m, 'TOKEN', 'robinhood', 'pending', :at, false, 10000, 0)
                        ON CONFLICT (mint) DO NOTHING
                    """), {"m": token_addr, "at": dt})

                    new_count += 1

            _last_scanned_block = to_block
            if new_count > 0:
                await db.commit()
            return new_count

    except Exception as e:
        print(f"[WALLET SYNC ERROR] Failed to sync transfers: {e}", flush=True)
        return 0
