import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta, timezone

from app.services.onchain_dataset import SyncPoint, barrier_outcome, first_entry, holders_at

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
A, B, C, PAIR, ZERO = ("0x" + c * 40 for c in "abcd0")


def path(*steps):
    """steps: (hours after T0, market cap); one Sync per block."""
    return [SyncPoint(100 + i, 0, T0 + timedelta(hours=h), mc, 5_000.0) for i, (h, mc) in enumerate(steps)]


class TestEntry(unittest.TestCase):
    def test_first_crossing_only(self):
        pts = path((0, 4_000), (1, 10_500), (2, 8_000), (3, 11_000))
        self.assertEqual(first_entry(pts), 1)

    def test_never_crossed(self):
        self.assertIsNone(first_entry(path((0, 4_000), (1, 9_999))))


class TestBarrier(unittest.TestCase):
    def test_tp_first(self):
        pts = path((0, 10_000), (5, 31_000), (6, 4_000))
        o = barrier_outcome(pts, 0, T0 + timedelta(hours=60))
        self.assertEqual(o["first_barrier"], "tp")
        self.assertTrue(o["label_30k_48h"])
        self.assertEqual(o["sl_block"], 102)

    def test_sl_first_but_label_counts_later_tp(self):
        # Shadow metric: SL hit first, yet the token still reached $30K inside the horizon
        pts = path((0, 10_000), (2, 4_900), (10, 30_000))
        o = barrier_outcome(pts, 0, T0 + timedelta(hours=60))
        self.assertEqual(o["first_barrier"], "sl")
        self.assertTrue(o["label_30k_48h"])

    def test_same_block_ordered_by_log_index(self):
        pts = [SyncPoint(100, 0, T0, 10_000, 1), SyncPoint(101, 3, T0, 4_000, 1), SyncPoint(101, 7, T0, 35_000, 1)]
        self.assertEqual(barrier_outcome(pts, 0, T0 + timedelta(hours=60))["first_barrier"], "sl")

    def test_tp_after_horizon_ignored(self):
        pts = path((0, 10_000), (49, 40_000))
        o = barrier_outcome(pts, 0, T0 + timedelta(hours=60))
        self.assertEqual(o["first_barrier"], "timeout")
        self.assertFalse(o["label_30k_48h"])
        self.assertEqual(o["peak_mc_h"], 10_000)

    def test_open_window_is_undecided(self):
        pts = path((0, 10_000), (3, 20_000))
        o = barrier_outcome(pts, 0, T0 + timedelta(hours=10))
        self.assertIsNone(o["label_30k_48h"])
        self.assertIsNone(o["first_barrier"])
        self.assertFalse(o["window_closed"])

    def test_open_window_decided_by_tp(self):
        pts = path((0, 10_000), (3, 30_000))
        self.assertTrue(barrier_outcome(pts, 0, T0 + timedelta(hours=10))["label_30k_48h"])


class TestHolders(unittest.TestCase):
    def test_excludes_zero_and_pair(self):
        tr = [
            {"from": ZERO, "to": PAIR, "amount_wei": 1000},
            {"from": PAIR, "to": A, "amount_wei": 100},
            {"from": PAIR, "to": B, "amount_wei": 50},
            {"from": B, "to": C, "amount_wei": 50},  # B sold everything to C
        ]
        self.assertEqual(holders_at(tr, {ZERO, PAIR}), 2)  # A and C


if __name__ == "__main__":
    unittest.main()
