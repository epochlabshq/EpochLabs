import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta, timezone

from app.services.desk import watching_rows
from app.services.live_market import best_pairs, dexscreener_url

TOKEN = "0x" + "aa" * 20


def pair(token, liq, mc, url="https://dexscreener.com/robinhood/0xpair"):
    return {"baseToken": {"address": token.upper().replace("0X", "0x")}, "liquidity": {"usd": liq},
            "marketCap": mc, "priceUsd": "0.00001", "url": url, "dexId": "uniswap"}


def row(mc_now, survival=0.7):
    return {"mint": TOKEN, "name": "A", "symbol": "A", "peak_mc": 20000, "launched_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "holders": 12, "survival": survival, "mc_now": mc_now, "pair_url": None, "mc_at": None}


class TestMarket(unittest.TestCase):
    def test_deepest_pair_wins(self):
        m = best_pairs([pair(TOKEN, 100, 5000, "u1"), pair(TOKEN, 9000, 21000, "u2"), pair(TOKEN, 50, 1, "u3")])
        self.assertEqual((m[TOKEN]["mc_usd"], m[TOKEN]["pair_url"]), (21000.0, "u2"))

    def test_fdv_used_when_market_cap_missing(self):
        p = pair(TOKEN, 10, None)
        p["fdv"] = 12345
        self.assertEqual(best_pairs([p])[TOKEN]["mc_usd"], 12345.0)

    def test_dexscreener_link(self):
        self.assertEqual(dexscreener_url(TOKEN.upper().replace("0X", "0x")), f"https://dexscreener.com/robinhood/{TOKEN}")
        r = watching_rows([row(15000)], 0.65, frozenset(), 50)[0]
        self.assertEqual(r["token"]["dexscreener_url"], f"https://dexscreener.com/robinhood/{TOKEN}")
        self.assertEqual(r["mc_now"], 15000.0)

    def test_reaching_take_profit_changes_status(self):
        self.assertEqual(watching_rows([row(29999)], 0.65, frozenset(), 50)[0]["status"], "scoring")
        tp = watching_rows([row(30000, survival=0.9)], 0.65, frozenset(), 50)[0]
        self.assertEqual((tp["status"], tp["survival"]), ("reached_tp", None))  # never shows a score once at TP


if __name__ == "__main__":
    unittest.main()
