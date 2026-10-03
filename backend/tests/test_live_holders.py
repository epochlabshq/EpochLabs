import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta, timezone

from app.services.chain_reader import TOPIC_TRANSFER, address_topic
from app.services.live_holders import DEAD, ZERO, block_at, holders_from_asset_transfers, holders_from_transfers

TOKEN = "0x" + "e5" * 20
A, B, C, POOL = ("0x" + c * 20 for c in ("a1", "b2", "c3", "d4"))


def log(frm, to, amount):
    return {"address": TOKEN, "topics": [TOPIC_TRANSFER, address_topic(frm), address_topic(to)], "data": hex(amount)}


def xfer(frm, to, amount):
    return {"from": frm, "to": to, "rawContract": {"value": hex(amount)}}


MOVES = [(ZERO, POOL, 1000), (POOL, A, 100), (POOL, B, 50), (A, C, 100), (B, DEAD, 10), (POOL, A, 0)]


class TestHolders(unittest.TestCase):
    def test_counts_positive_balances_only(self):
        # A sent everything to C: A is gone. Zero and dead addresses never count. Zero-amount moves change nothing.
        self.assertEqual(holders_from_transfers([log(*m) for m in MOVES]), 3)  # POOL, B, C

    def test_both_sources_agree(self):
        self.assertEqual(holders_from_asset_transfers([xfer(*m) for m in MOVES]),
                         holders_from_transfers([log(*m) for m in MOVES]))

    def test_ignores_other_events(self):
        noise = {"address": TOKEN, "topics": ["0x" + "11" * 32], "data": "0x"}
        self.assertEqual(holders_from_transfers([noise, log(ZERO, A, 5)]), 1)

    def test_block_at(self):
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertEqual(block_at(now - timedelta(seconds=10), 1000, now, 0.1), 900)
        self.assertEqual(block_at(now + timedelta(hours=1), 1000, now, 0.1), 1000)  # never past latest
        self.assertEqual(block_at(now - timedelta(days=9999), 1000, now, 0.1), 0)


if __name__ == "__main__":
    unittest.main()
