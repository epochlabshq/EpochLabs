"""
Golem's trading rules. Pure functions (no I/O): candidate selection, liquidity and market-cap checks, sizing,
exit rules, and Uniswap V2 swap calldata. The executor (app.services.desk_executor) feeds them chain data.

Parameters are the DESK_* settings; their values are placeholders until the Open questions are decided.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

WEI = 10 ** 18

# Uniswap V2 router (fee-on-transfer safe variants: many launchpad tokens tax transfers)
SEL_SWAP_EXACT_ETH_FOR_TOKENS = "0xb6f9de95"  # swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)
SEL_SWAP_EXACT_TOKENS_FOR_ETH = "0x791ac947"  # swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)
SEL_APPROVE = "0x095ea7b3"                    # approve(address,uint256)


# ---------------------------------------------------------------------------
# Market numbers from a WETH pair
# ---------------------------------------------------------------------------

def mc_usd(weth_reserve: int, token_reserve: int, total_supply: int, eth_usd: float) -> Optional[float]:
    """Market cap: mid price (WETH per raw token unit) x total supply x ETH/USD. Token decimals cancel out."""
    if not token_reserve:
        return None
    return total_supply * weth_reserve / token_reserve / WEI * eth_usd


def liquidity_usd(weth_reserve: int, eth_usd: float) -> float:
    """Both sides of the pool: 2 x WETH reserve x ETH/USD (same definition as the onchain dataset)."""
    return 2 * weth_reserve / WEI * eth_usd


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def select_candidates(scored: list[dict], *, threshold: float, excluded: frozenset[str], busy: set[str],
                      open_count: int, queued_count: int, max_open: int) -> list[dict]:
    """
    scored: Watching rows with `mint` and `survival` from the live model run.
    Best survival first, never a team token, never one already held or queued, never past the position cap.
    """
    slots = max(0, max_open - open_count - queued_count)
    eligible = [
        s for s in scored
        if s.get("survival") is not None and s["survival"] >= threshold
        and s["mint"].lower() not in excluded and s["mint"].lower() not in busy
    ]
    eligible.sort(key=lambda s: -s["survival"])
    return eligible[:slots]


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: Optional[str] = None


def liquidity_check(*, weth_reserve: Optional[int], token_reserve: Optional[int], total_supply: Optional[int],
                    eth_usd: Optional[float], min_liq_usd: float, stop_loss_mc_usd: float,
                    take_profit_mc_usd: float) -> Check:
    """
    A candidate must have a readable WETH pair, enough liquidity (BlastBack had under $1) and a market cap
    between the stop-loss and the take-profit, otherwise an exit would fire at once.
    """
    if eth_usd is None:
        return Check(False, "no ETH/USD price")
    if not weth_reserve or not token_reserve or not total_supply:
        return Check(False, "no readable WETH pair")
    liq = liquidity_usd(weth_reserve, eth_usd)
    if liq < min_liq_usd:
        return Check(False, f"liquidity below ${min_liq_usd:,.0f}")
    mc = mc_usd(weth_reserve, token_reserve, total_supply, eth_usd)
    if mc <= stop_loss_mc_usd:
        return Check(False, "market cap at or below the stop-loss")
    if mc >= take_profit_mc_usd:
        return Check(False, "market cap already at the take-profit")
    return Check(True)


@dataclass(frozen=True)
class Sizing:
    size_wei: Optional[int]
    rule: str
    reason: Optional[str] = None  # why no position, when size_wei is None


def size_position(balance_wei: Optional[int], *, size_eth: float, max_fraction: float, gas_reserve_eth: float) -> Sizing:
    """Fixed size, capped at a fraction of the wallet, always leaving gas for the exits."""
    rule = f"fixed {size_eth:g} ETH, at most {max_fraction:.0%} of the wallet, {gas_reserve_eth:g} ETH kept for gas"
    if balance_wei is None:
        return Sizing(None, rule, "wallet balance unreadable")
    spendable = balance_wei - int(gas_reserve_eth * WEI)
    size = min(int(size_eth * WEI), int(balance_wei * max_fraction), spendable)
    if size <= 0 or size < int(size_eth * WEI) // 2:
        return Sizing(None, rule, "wallet too small for the position size")
    return Sizing(size, rule)


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

def exit_reason(*, mc_now: Optional[float], entered_at: datetime, now: datetime, take_profit_mc_usd: float,
                stop_loss_mc_usd: float, max_hold_h: int) -> Optional[str]:
    """The exit rule that fires now, if any. The time limit still fires when the market cap is unreadable."""
    if mc_now is not None:
        if mc_now <= stop_loss_mc_usd:
            return "stop_loss"
        if mc_now >= take_profit_mc_usd:
            return "take_profit"
    if now - entered_at >= timedelta(hours=max_hold_h):
        return "max_hold"
    return None


# ---------------------------------------------------------------------------
# Swap amounts and calldata (Uniswap V2)
# ---------------------------------------------------------------------------

def amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    """UniswapV2Library.getAmountOut (0.3% fee)."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    fee_in = amount_in * 997
    return fee_in * reserve_out // (reserve_in * 1000 + fee_in)


def min_out(amount_in: int, reserve_in: int, reserve_out: int, slippage_bps: int) -> int:
    return amount_out(amount_in, reserve_in, reserve_out) * (10_000 - slippage_bps) // 10_000


def _word(v: int) -> str:
    return f"{v:064x}"


def _addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def encode_buy(amount_out_min: int, weth: str, token: str, to: str, deadline: int) -> str:
    """swapExactETHForTokensSupportingFeeOnTransferTokens(amountOutMin, [WETH, token], to, deadline)."""
    return (SEL_SWAP_EXACT_ETH_FOR_TOKENS + _word(amount_out_min) + _word(4 * 32) + _addr(to) + _word(deadline)
            + _word(2) + _addr(weth) + _addr(token))


def encode_sell(amount_in: int, amount_out_min: int, token: str, weth: str, to: str, deadline: int) -> str:
    """swapExactTokensForETHSupportingFeeOnTransferTokens(amountIn, amountOutMin, [token, WETH], to, deadline)."""
    return (SEL_SWAP_EXACT_TOKENS_FOR_ETH + _word(amount_in) + _word(amount_out_min) + _word(5 * 32) + _addr(to)
            + _word(deadline) + _word(2) + _addr(token) + _addr(weth))


def encode_approve(spender: str, amount: int) -> str:
    return SEL_APPROVE + _addr(spender) + _word(amount)


@dataclass(frozen=True)
class TxRequest:
    to: str
    data: str
    value_wei: int


def build_buy_tx(*, size_wei: int, weth_reserve: int, token_reserve: int, weth: str, token: str, wallet: str,
                 router: str, slippage_bps: int, deadline: int) -> TxRequest:
    out_min = min_out(size_wei, weth_reserve, token_reserve, slippage_bps)
    if out_min <= 0:
        raise ValueError("buy would return nothing at current reserves")
    return TxRequest(router, encode_buy(out_min, weth, token, wallet, deadline), size_wei)


def build_sell_tx(*, amount_wei: int, weth_reserve: int, token_reserve: int, weth: str, token: str, wallet: str,
                  router: str, slippage_bps: int, deadline: int) -> TxRequest:
    out_min = min_out(amount_wei, token_reserve, weth_reserve, slippage_bps)
    return TxRequest(router, encode_sell(amount_wei, out_min, token, weth, wallet, deadline), 0)
