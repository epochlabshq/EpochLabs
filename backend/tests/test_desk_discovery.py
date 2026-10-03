import unittest
from datetime import datetime, timezone

from app.services.desk_discovery import qualifying

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
ADDR = "0x" + "ab" * 20


def pair(**kw):
    p = {"chainId": "robinhood", "baseToken": {"address": ADDR, "name": "A", "symbol": "A"}, "priceUsd": "0.00002",
         "marketCap": 15000, "liquidity": {"usd": 9000}, "pairCreatedAt": int((NOW.timestamp() - 3600) * 1000)}
    p.update(kw)
    return p


def run(*pairs, excluded=frozenset()):
    return qualifying(list(pairs), NOW, min_mc=10_000, max_mc=30_000, min_liq=5_000, max_age_h=48, excluded=excluded)


class TestQualifying(unittest.TestCase):
    def test_accepts_a_token_in_the_band(self):
        self.assertIn(ADDR.lower(), run(pair()))

    def test_rejects_by_criteria(self):
        for bad in (pair(chainId="solana"), pair(marketCap=9_000), pair(marketCap=30_000),
                    pair(liquidity={"usd": 100}), pair(pairCreatedAt=int((NOW.timestamp() - 49 * 3600) * 1000)),
                    pair(baseToken={"address": "not-an-address"}), pair(priceUsd=None)):
            self.assertEqual(run(bad), {})

    def test_team_tokens_are_excluded(self):
        self.assertEqual(run(pair(), excluded=frozenset({ADDR.lower()})), {})

    def test_deepest_pair_wins(self):
        out = run(pair(), pair(liquidity={"usd": 20_000}))
        self.assertEqual(out[ADDR.lower()]["liq_usd"], 20_000)


if __name__ == "__main__":
    unittest.main()
