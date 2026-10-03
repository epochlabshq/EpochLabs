import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app.core.config import settings
from app.services import desk_executor
from app.services.desk_trading import (
    amount_out, build_buy_tx, build_sell_tx, encode_buy, encode_sell, exit_reason, liquidity_check, liquidity_usd,
    mc_usd, min_out, select_candidates, size_position,
)

E = 10 ** 18
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
WETH = "0x" + "c3" * 20
TOKEN = "0x" + "e5" * 20
WALLET = "0x" + "a1" * 20
ROUTER = "0x" + "b2" * 20


class TestMarket(unittest.TestCase):
    def test_mc_and_liquidity(self):
        # 2 WETH vs 100M tokens, supply 1B, ETH at $2,500: price 2e-8 ETH -> MC 20 ETH = $50K; liq $10K
        self.assertAlmostEqual(mc_usd(2 * E, 100_000_000 * E, 1_000_000_000 * E, 2500), 50_000)
        self.assertAlmostEqual(liquidity_usd(2 * E, 2500), 10_000)
        self.assertIsNone(mc_usd(E, 0, E, 2500))

    def _check(self, weth, supply=1_000_000_000 * E, eth=2500.0):
        return liquidity_check(weth_reserve=weth, token_reserve=100_000_000 * E, total_supply=supply, eth_usd=eth,
                               min_liq_usd=5000, stop_loss_mc_usd=5000, take_profit_mc_usd=30000)

    def test_liquidity_check(self):
        self.assertTrue(self._check(E).ok)                       # MC $25K, liq $5K
        self.assertEqual(self._check(E // 2).reason, "liquidity below $5,000")  # BlastBack-like pools
        self.assertEqual(self._check(2 * E).reason, "market cap already at the take-profit")
        self.assertEqual(self._check(E, supply=10_000_000 * E).reason, "market cap at or below the stop-loss")
        self.assertEqual(self._check(E, eth=None).reason, "no ETH/USD price")
        self.assertEqual(self._check(None).reason, "no readable WETH pair")


class TestSelection(unittest.TestCase):
    def test_best_first_within_slots_and_never_excluded_or_busy(self):
        scored = [{"mint": m, "survival": s} for m, s in
                  [("0xA", 0.70), ("0xB", 0.90), ("0xC", 0.80), ("0xD", 0.60), ("0xE", None), ("0xEPC", 0.99)]]
        picks = select_candidates(scored, threshold=0.65, excluded=frozenset({"0xepc"}), busy={"0xb"},
                                  open_count=1, queued_count=0, max_open=3)
        self.assertEqual([p["mint"] for p in picks], ["0xC", "0xA"])

    def test_no_slots_when_full(self):
        scored = [{"mint": "0xA", "survival": 0.9}]
        self.assertEqual(select_candidates(scored, threshold=0.65, excluded=frozenset(), busy=set(),
                                           open_count=2, queued_count=1, max_open=3), [])


class TestSizing(unittest.TestCase):
    def test_fixed_size_capped_by_wallet_and_gas(self):
        kw = dict(size_eth=0.05, max_fraction=0.2, gas_reserve_eth=0.005)
        self.assertEqual(size_position(E, **kw).size_wei, 5 * E // 100)
        self.assertEqual(size_position(E // 5, **kw).size_wei, 4 * E // 100)   # 20% of 0.2 ETH
        self.assertIsNone(size_position(E // 50, **kw).size_wei)               # 0.02 ETH wallet: too small
        self.assertEqual(size_position(None, **kw).reason, "wallet balance unreadable")
        self.assertIn("0.05 ETH", size_position(E, **kw).rule)


class TestExits(unittest.TestCase):
    def _r(self, mc, held_h=1):
        return exit_reason(mc_now=mc, entered_at=NOW - timedelta(hours=held_h), now=NOW,
                           take_profit_mc_usd=30000, stop_loss_mc_usd=5000, max_hold_h=48)

    def test_rules(self):
        self.assertIsNone(self._r(15000))
        self.assertEqual(self._r(31000), "take_profit")
        self.assertEqual(self._r(4000), "stop_loss")
        self.assertEqual(self._r(15000, held_h=48), "max_hold")
        self.assertEqual(self._r(None, held_h=49), "max_hold")  # time limit works without a price
        self.assertIsNone(self._r(None))


class TestSwaps(unittest.TestCase):
    def test_amount_out_matches_uniswap_v2(self):
        # Reference: getAmountOut(1e18, 10e18, 10e18) with the 0.3% fee
        self.assertEqual(amount_out(E, 10 * E, 10 * E), 906610893880149131)
        self.assertEqual(min_out(E, 10 * E, 10 * E, 500), 906610893880149131 * 9500 // 10000)

    def test_buy_calldata_layout(self):
        data = encode_buy(123, WETH, TOKEN, WALLET, 1_700_000_000)
        words = [data[10 + 64 * i: 10 + 64 * (i + 1)] for i in range((len(data) - 10) // 64)]
        self.assertEqual(data[:10], "0xb6f9de95")
        self.assertEqual(int(words[0], 16), 123)
        self.assertEqual(int(words[1], 16), 128)            # offset of the path array
        self.assertEqual("0x" + words[2][-40:], WALLET)
        self.assertEqual(int(words[3], 16), 1_700_000_000)
        self.assertEqual(int(words[4], 16), 2)
        self.assertEqual(["0x" + w[-40:] for w in words[5:]], [WETH, TOKEN])

    def test_sell_calldata_layout(self):
        data = encode_sell(10 * E, 5, TOKEN, WETH, WALLET, 99)
        words = [data[10 + 64 * i: 10 + 64 * (i + 1)] for i in range((len(data) - 10) // 64)]
        self.assertEqual(data[:10], "0x791ac947")
        self.assertEqual([int(words[0], 16), int(words[1], 16), int(words[2], 16)], [10 * E, 5, 160])
        self.assertEqual(["0x" + w[-40:] for w in words[6:]], [TOKEN, WETH])

    def test_tx_builders(self):
        buy = build_buy_tx(size_wei=E // 20, weth_reserve=2 * E, token_reserve=100_000_000 * E, weth=WETH,
                           token=TOKEN, wallet=WALLET, router=ROUTER, slippage_bps=500, deadline=1)
        self.assertEqual((buy.to, buy.value_wei), (ROUTER, E // 20))
        sell = build_sell_tx(amount_wei=E, weth_reserve=2 * E, token_reserve=100 * E, weth=WETH, token=TOKEN,
                             wallet=WALLET, router=ROUTER, slippage_bps=500, deadline=1)
        self.assertEqual(sell.value_wei, 0)
        with self.assertRaises(ValueError):
            build_buy_tx(size_wei=1, weth_reserve=E, token_reserve=1, weth=WETH, token=TOKEN, wallet=WALLET,
                         router=ROUTER, slippage_bps=500, deadline=1)


class TestExecutorSafety(unittest.TestCase):
    def test_off_and_live_without_signer_do_nothing(self):
        # db=None: any DB access would raise, so returning cleanly proves nothing was touched
        desk_executor.register_signer(None)
        for mode in ("off", "live", "bogus"):
            with mock.patch.object(settings, "DESK_EXECUTOR_MODE", mode):
                asyncio.run(desk_executor.run_executor_cycle(None, None))

    def test_signer_must_be_the_golem_wallet(self):
        with self.assertRaises(ValueError):
            desk_executor.register_signer(SimpleNamespace(address="0x" + "99" * 20))
        desk_executor.register_signer(SimpleNamespace(address=settings.GOLEM_WALLET.upper().replace("0X", "0x")))
        desk_executor.register_signer(None)


if __name__ == "__main__":
    unittest.main()
