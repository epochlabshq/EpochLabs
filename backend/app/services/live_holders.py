"""
Holder counts read from the chain: replay every Transfer of the token and count addresses left with a positive
balance (the zero address and the dead address excluded). Blockscout sits behind a Cloudflare challenge, so the
source is the public RPC (address-filtered eth_getLogs over a young token's whole life in one call), with
Alchemy's asset-transfers API as a fallback (its free tier caps eth_getLogs at 10 blocks, but not transfers).

Two uses:
- live counts for the Desk's Watching feed (`desk_live_holders`), refreshed a batch per Desk worker cycle;
- the 48h label sample (app.services.holder_sampler), counted at the block nearest launch + 48h.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from app.services.chain_reader import TOPIC_TRANSFER, ChainReader, decode_transfer

ZERO = "0x" + "0" * 40
DEAD = "0x000000000000000000000000000000000000dead"
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Replay window, ending at the sample block. tokens.launched_at is when the feed first saw the token, not when
# it was created (DIVIX: first transfer ~42h before a launched_at of 20h), so the window cannot start from it.
# The public node allows 10M blocks (~11.8 days at 0.1s) per address-filtered eth_getLogs.
LOOKBACK_BLOCKS = 9_999_999
MAX_SPLIT_DEPTH = 12
CONCURRENCY = 5

_block_time: Optional[tuple[int, datetime, float]] = None  # (latest block, its time, seconds per block)


def _count_positive(moves) -> int:
    """moves: (from, to, amount) triples. Final balances do not depend on order."""
    balances: dict[str, int] = {}
    for frm, to, amount in moves:
        if amount == 0:
            continue
        balances[frm] = balances.get(frm, 0) - amount
        balances[to] = balances.get(to, 0) + amount
    return sum(1 for a, b in balances.items() if b > 0 and a not in (ZERO, DEAD))


def holders_from_transfers(logs: list[dict]) -> int:
    """From raw Transfer logs."""
    moves = []
    for lg in logs:
        if not lg.get("topics") or lg["topics"][0] != TOPIC_TRANSFER or len(lg["topics"]) < 3:
            continue
        t = decode_transfer(lg)
        moves.append((t["from"], t["to"], t["amount_wei"]))
    return _count_positive(moves)


def holders_from_asset_transfers(transfers: list[dict]) -> int:
    """From alchemy_getAssetTransfers results (raw amounts in rawContract.value)."""
    return _count_positive(
        (t["from"].lower(), (t.get("to") or ZERO).lower(), int(t["rawContract"]["value"] or "0x0", 16))
        for t in transfers
    )


async def _clock(r: ChainReader) -> tuple[int, datetime, float]:
    """Latest block, its timestamp, and the average block time over the last 100k blocks (cached 10 min)."""
    global _block_time
    if _block_time and datetime.now(timezone.utc) - _block_time[1] < timedelta(minutes=10):
        return _block_time
    latest = await r.block_number()
    ts = await r.block_timestamps({latest, latest - 100_000})
    bt = (ts[latest] - ts[latest - 100_000]).total_seconds() / 100_000
    _block_time = (latest, ts[latest], bt)
    return _block_time


def block_at(when: datetime, latest: int, latest_at: datetime, seconds_per_block: float) -> int:
    return max(0, min(latest, latest - int((latest_at - when).total_seconds() / seconds_per_block)))


async def _logs(r: ChainReader, token: str, frm: int, to: int, depth: int = 0) -> list[dict]:
    try:
        return await r.get_logs(frm, to, address=token, topics=[TOPIC_TRANSFER])
    except Exception:
        # Over the node's 10k-logs-per-call limit: split the range
        if depth >= MAX_SPLIT_DEPTH or to - frm < 2:
            raise
        mid = (frm + to) // 2
        return await _logs(r, token, frm, mid, depth + 1) + await _logs(r, token, mid + 1, to, depth + 1)


async def _alchemy_transfers(r: ChainReader, token: str, to_block: int) -> list[dict]:
    out, page = [], None
    for _ in range(200):  # 200k transfers at most
        params = {"fromBlock": "0x0", "toBlock": hex(to_block), "contractAddresses": [token], "category": ["erc20"],
                  "withMetadata": False, "excludeZeroValue": True, "maxCount": hex(1000)}
        if page:
            params["pageKey"] = page
        res = await r._call("alchemy_getAssetTransfers", [params])
        out += res["transfers"]
        page = res.get("pageKey")
        if not page:
            return out
    raise RuntimeError("too many transfers to page through")


async def count_holders(logs_r: Optional[ChainReader], token: str, launched_at: datetime,
                        at: Optional[datetime] = None,
                        transfers_r: Optional[ChainReader] = None) -> Optional[tuple[int, int]]:
    """(holders, block) at `at` (default: now), or None when no source could be read."""
    token = token.lower()
    clock_r = logs_r or transfers_r
    if clock_r is None:
        return None
    try:
        latest, latest_at, bt = await _clock(clock_r)
    except Exception as e:
        print(f"[HOLDERS] block clock unavailable: {type(e).__name__} {e}", flush=True)
        return None
    to = latest if at is None else block_at(at, latest, latest_at, bt)
    if logs_r is not None:
        try:
            frm = max(0, to - LOOKBACK_BLOCKS)
            return holders_from_transfers(await _logs(logs_r, token, frm, to)), to
        except Exception as e:
            print(f"[HOLDERS] {token} via logs: {type(e).__name__} {e}", flush=True)
    if transfers_r is not None:
        try:
            return holders_from_asset_transfers(await _alchemy_transfers(transfers_r, token, to)), to
        except Exception as e:
            print(f"[HOLDERS] {token} via Alchemy transfers: {type(e).__name__} {e}", flush=True)
    return None


MAX_REPLAY_LOGS = 60_000      # a bigger token is skipped rather than replayed (memory and RPC time)
REPLAY_TIMEOUT_S = 150.0


async def count_holders_full(logs_r: Optional[ChainReader], token: str, created_at: datetime,
                             transfers_r: Optional[ChainReader] = None) -> Optional[tuple[int, int]]:
    """
    (holders, block) now, replaying the token's whole life in consecutive windows (count_holders reads only one,
    about 11.8 days). Balances are folded in as each window arrives, and a token with more than MAX_REPLAY_LOGS
    transfers, or a replay past REPLAY_TIMEOUT_S, returns None: no number is better than a partial one.
    """
    token = token.lower()
    clock_r = logs_r or transfers_r
    if clock_r is None:
        return None
    try:
        latest, latest_at, bt = await _clock(clock_r)
    except Exception as e:
        print(f"[HOLDERS] block clock unavailable: {type(e).__name__} {e}", flush=True)
        return None
    frm = max(0, block_at(created_at, latest, latest_at, bt) - 20_000)  # margin for the clock estimate
    if latest - frm <= LOOKBACK_BLOCKS:
        return await count_holders(logs_r, token, created_at, transfers_r=transfers_r)
    if logs_r is None:
        return None

    async def replay() -> Optional[tuple[int, int]]:
        balances: dict[str, int] = {}
        seen = 0
        start = frm
        while start <= latest:
            end = min(latest, start + LOOKBACK_BLOCKS)
            for lg in await _logs(logs_r, token, start, end):
                if not lg.get("topics") or lg["topics"][0] != TOPIC_TRANSFER or len(lg["topics"]) < 3:
                    continue
                t = decode_transfer(lg)
                if t["amount_wei"]:
                    balances[t["from"]] = balances.get(t["from"], 0) - t["amount_wei"]
                    balances[t["to"]] = balances.get(t["to"], 0) + t["amount_wei"]
                seen += 1
            if seen > MAX_REPLAY_LOGS:
                print(f"[HOLDERS] {token}: over {MAX_REPLAY_LOGS} transfers, full replay skipped", flush=True)
                return None
            start = end + 1
        return sum(1 for a, b in balances.items() if b > 0 and a not in (ZERO, DEAD)), latest

    try:
        return await asyncio.wait_for(replay(), REPLAY_TIMEOUT_S)
    except Exception as e:
        print(f"[HOLDERS] {token} full replay: {type(e).__name__} {e}", flush=True)
        return None


async def has_code(r: ChainReader, addresses: list[str]) -> dict[str, bool]:
    """Whether a contract exists at each address (small batches: Alchemy's free tier limits compute per second)."""
    out = {}
    for i in range(0, len(addresses), 10):
        chunk = addresses[i:i + 10]
        res = await r._batch([("eth_getCode", [a.lower(), "latest"]) for a in chunk])
        out.update({a: c not in (None, "0x") for a, c in zip(chunk, res)})
    return out


async def refresh_live_holders(db, logs_r: Optional[ChainReader], transfers_r: Optional[ChainReader], *,
                               max_age_h: int, limit: int, code_r: Optional[ChainReader] = None) -> int:
    """
    Recount the stalest `limit` tokens of the Watching feed. Returns how many were updated.
    A feed row whose address has no contract on Robinhood Chain is recorded with has_code = false.
    """
    rows = (await db.execute(text(
        "SELECT t.mint, t.launched_at FROM tokens t LEFT JOIN desk_live_holders h ON h.mint = t.mint "
        "WHERE t.status::text = 'pending' AND t.chain = 'robinhood' AND t.mint ~ '^0x[0-9a-fA-F]{40}$' "
        "AND t.launched_at > now() - make_interval(hours => :h) "
        "AND (h.sampled_at IS NULL OR h.sampled_at < now() - interval '30 minutes') "
        "ORDER BY h.sampled_at NULLS FIRST LIMIT :lim"
    ), {"h": max_age_h, "lim": limit})).all()
    if not rows:
        return 0
    code = await has_code(code_r or logs_r or transfers_r, [m for m, _ in rows]) if rows else {}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(mint, launched_at):
        async with sem:
            return mint, await count_holders(logs_r, mint, launched_at, transfers_r=transfers_r)

    results = await asyncio.gather(*(one(m, at) for m, at in rows if code.get(m)))
    results += [(m, None) for m, _ in rows if not code.get(m)]
    now = datetime.now(timezone.utc)
    
    params = [
        {
            "m": mint,
            "h": res[0] if res else 0,
            "b": res[1] if res else 0,
            "at": now,
            "c": bool(code.get(mint)),
        }
        for mint, res in results
        if not (res is None and code.get(mint))
    ]
    if params:
        await db.execute(text("""
            INSERT INTO desk_live_holders (mint, holders, block, sampled_at, has_code)
            VALUES (:m, :h, :b, :at, :c) ON CONFLICT (mint) DO UPDATE SET holders = EXCLUDED.holders,
            block = EXCLUDED.block, sampled_at = EXCLUDED.sampled_at, has_code = EXCLUDED.has_code
        """), params)
        await db.commit()
    return len(params)
