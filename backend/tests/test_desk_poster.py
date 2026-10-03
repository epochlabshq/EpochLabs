import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import timedelta

from app.api.desk_endpoints import find_trade
from app.services.desk_poster import (
    DISCLAIMER, initial_post_status, next_post_status, render_entry_post, render_exit_post, strip_cashtags, x_length,
)
from test_desk import E, NOW, TOKEN, decision, inputs, swap, why

DESK = "https://epochlabs.run/desk"
BUY = "0x" + "b1" * 32
SELL = "0x" + "51" * 32


def trades(symbol="MDOG", why_card=None, exit_reason="take_profit", sell_eth=E // 5):
    w = why_card or why()
    inp = inputs(
        swaps=[swap(BUY, 1, "buy", TOKEN, 1000 * E, E // 10), swap(SELL, 2, "sell", TOKEN, 1000 * E, sell_eth, minutes=200)],
        meta={TOKEN: {"name": "Moon Dog", "symbol": symbol, "status": "passed"}},
        decisions={BUY: decision(1, "buy", w), SELL: decision(2, "sell", exit_reason=exit_reason)},
    )
    return find_trade(inp, "t_0001")


class TestEntryPost(unittest.TestCase):
    def test_matches_the_why_card(self):
        t = trades()
        post = render_entry_post(t, DESK)
        w = t["why"]
        self.assertIn("Golem entered MDOG", post)
        self.assertIn(f"survival {w['survival']:.2f} (threshold {w['threshold']:.2f})", post)
        self.assertIn(f"Size {w['size_eth']:.2f} ETH", post)
        for s in w["top_signals"]:
            self.assertIn(s["value"], post)
        self.assertIn(t["entry"]["tx_url"], post)
        self.assertIn(DESK, post)
        self.assertTrue(post.endswith(DISCLAIMER))
        self.assertLessEqual(x_length(post), 280)

    def test_no_cashtags_anywhere(self):
        w = why()
        w["top_signals"][2]["value"] = '"buy $EPC and $MDOG"'
        post = render_entry_post(trades(symbol="$MDOG", why_card=w), DESK)
        self.assertNotRegex(post, r"\$[A-Za-z]")
        self.assertIn("EPC", post)

    def test_long_signals_are_trimmed_to_fit(self):
        w = why()
        for s in w["top_signals"]:
            s["value"] = "x" * 200
        post = render_entry_post(trades(why_card=w), DESK)
        self.assertLessEqual(x_length(post), 280)
        self.assertIn(DESK, post)

    def test_trade_without_why_is_not_posted(self):
        t = trades()
        t["why"] = None
        with self.assertRaises(ValueError):
            render_entry_post(t, DESK)


class TestExitPost(unittest.TestCase):
    def test_win(self):
        post = render_exit_post(trades(), DESK)
        self.assertIn("Golem closed MDOG: +0.1000 ETH (+100.0%)", post)
        self.assertIn("Held 3h 20m · take-profit", post)
        self.assertIn("EPC burned: pending burn rule", post)
        self.assertIn("tx https://", post)
        self.assertLessEqual(x_length(post), 280)

    def test_loss_shows_minus(self):
        post = render_exit_post(trades(exit_reason="stop_loss", sell_eth=E // 25), DESK)
        self.assertIn("-0.0600 ETH (-60.0%)", post)
        self.assertIn("stop-loss", post)


class TestQueueRules(unittest.TestCase):
    def test_retry_then_give_up(self):
        self.assertEqual(next_post_status("failed", 1, 3), "pending")
        self.assertEqual(next_post_status("failed", 2, 3), "pending")
        self.assertEqual(next_post_status("failed", 3, 3), "failed")
        self.assertEqual(next_post_status("sent", 1, 3), "posted")
        self.assertEqual(next_post_status("dry_run", 1, 3), "dry_run")

    def test_history_is_never_posted(self):
        self.assertEqual(initial_post_status(NOW - timedelta(hours=7), NOW, 6), "skipped")
        self.assertEqual(initial_post_status(NOW - timedelta(minutes=2), NOW, 6), "pending")

    def test_strip_cashtags_keeps_dollar_amounts(self):
        self.assertEqual(strip_cashtags("$EPC hit $30K"), "EPC hit $30K")

    def test_x_length_counts_urls_as_23(self):
        self.assertEqual(x_length("a https://example.com/very/long/path?x=1"), 2 + 23)


if __name__ == "__main__":
    unittest.main()
