import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from eth_account import Account
from eth_utils import keccak

from app.core.config import settings
from app.services import gf_chain, gf_launch, gf_poster
from app.services.chain_reader import TOPIC_TRANSFER, address_topic

NOW = datetime(2026, 10, 6, 20, 5, tzinfo=timezone.utc)
EPC = "0x" + "e1" * 20
BURN = gf_chain.DEAD_ADDRESS
WALLET = "0x" + "a1" * 20


def transfer_log(token=EPC, frm=WALLET, to=BURN, amount=10 ** 21):
    return {"address": token, "topics": [TOPIC_TRANSFER, address_topic(frm), address_topic(to)], "data": hex(amount)}


def receipt(*logs, status="0x1"):
    return {"status": status, "logs": list(logs), "blockNumber": "0x10"}


class TestFeeReceipt(unittest.TestCase):
    def check(self, r, need=10 ** 21):
        return gf_chain.check_fee_receipt(r, epc=EPC, burn=BURN, sender=WALLET, min_amount_wei=need)

    def test_exact_amount_passes(self):
        c = self.check(receipt(transfer_log()))
        self.assertEqual((c.ok, c.amount_wei, c.reason), (True, 10 ** 21, None))

    def test_more_than_the_fee_passes(self):
        self.assertTrue(self.check(receipt(transfer_log(amount=5 * 10 ** 21))).ok)

    def test_one_wei_short_fails(self):
        c = self.check(receipt(transfer_log(amount=10 ** 21 - 1)))
        self.assertEqual((c.ok, c.reason), (False, "amount_too_low"))

    def test_several_transfers_in_one_tx_add_up(self):
        self.assertTrue(self.check(receipt(transfer_log(amount=6 * 10 ** 20), transfer_log(amount=4 * 10 ** 20))).ok)

    def test_only_the_epc_contract_counts(self):
        c = self.check(receipt(transfer_log(token="0x" + "99" * 20)))
        self.assertEqual((c.ok, c.reason), (False, "no_epc_burn_from_wallet"))

    def test_the_sender_must_be_the_logged_in_wallet(self):
        c = self.check(receipt(transfer_log(frm="0x" + "b2" * 20)))
        self.assertEqual((c.ok, c.reason), (False, "no_epc_burn_from_wallet"))

    def test_the_recipient_must_be_the_burn_address(self):
        c = self.check(receipt(transfer_log(to="0x" + "c3" * 20)))
        self.assertEqual(c.reason, "no_epc_burn_from_wallet")

    def test_address_case_does_not_matter(self):
        log = transfer_log(token=EPC.upper().replace("0X", "0x"))
        self.assertTrue(gf_chain.check_fee_receipt(receipt(log), epc=EPC, burn=BURN.upper().replace("0X", "0x"),
                                                   sender=WALLET.upper().replace("0X", "0x"), min_amount_wei=1).ok)

    def test_failed_missing_and_empty_receipts(self):
        self.assertEqual(self.check(receipt(transfer_log(), status="0x0")).reason, "tx_failed")
        self.assertEqual(self.check(None).reason, "tx_not_found")
        self.assertEqual(self.check(receipt()).reason, "no_epc_burn_from_wallet")

    def test_other_events_and_short_topics_are_ignored(self):
        junk = [{"address": EPC, "topics": ["0xdeadbeef"], "data": "0x1"}, {"address": EPC, "topics": [], "data": "0x"},
                {"address": EPC, "topics": [TOPIC_TRANSFER, address_topic(WALLET)], "data": "0x1"}]
        self.assertEqual(self.check(receipt(*junk, transfer_log())).ok, True)

    def test_freshness(self):
        self.assertTrue(gf_chain.fee_tx_fresh(NOW - timedelta(hours=47), NOW))
        self.assertFalse(gf_chain.fee_tx_fresh(NOW - timedelta(hours=49), NOW))
        self.assertFalse(gf_chain.fee_tx_fresh(None, NOW))
        self.assertFalse(gf_chain.fee_tx_fresh(NOW + timedelta(hours=1), NOW))     # a block time in the future is nonsense


class TestUnits(unittest.TestCase):
    def test_epc_wei_conversion_has_no_float_error(self):
        self.assertEqual(gf_chain.epc_to_wei(1000, 18), 1000 * 10 ** 18)
        self.assertEqual(gf_chain.epc_to_wei(0.1, 18), 10 ** 17)
        self.assertEqual(gf_chain.epc_to_wei(1234.5678, 6), 1234567800)
        self.assertEqual(gf_chain.wei_to_epc(1500 * 10 ** 18, 18), 1500.0)
        self.assertAlmostEqual(gf_chain.wei_to_epc(10 ** 17, 18), 0.1)


class TestCalldata(unittest.TestCase):
    def test_selectors_match_the_solidity_signatures(self):
        self.assertEqual(gf_chain.SEL_DISTRIBUTE, "0x" + keccak(text="distribute(uint256)").hex()[:8])
        self.assertEqual(gf_chain.SEL_CREATE_SPLITTER, "0x" + keccak(text="create(address,bytes32)").hex()[:8])
        self.assertEqual(gf_chain.TOPIC_DISTRIBUTED, "0x" + keccak(text="Distributed(uint256,uint256,uint256)").hex())

    def test_distribute_calldata(self):
        data = gf_chain.distribute_calldata(255)
        self.assertEqual(len(data), 2 + 8 + 64)
        self.assertTrue(data.endswith("ff"))
        self.assertTrue(data.startswith(gf_chain.SEL_DISTRIBUTE))

    def test_create_splitter_calldata_uses_the_same_hash_as_the_script(self):
        data = gf_chain.create_splitter_calldata(WALLET, "20261006-ab12cd34")
        self.assertEqual(len(data), 2 + 8 + 64 + 64)
        self.assertIn(WALLET[2:], data)
        self.assertTrue(data.endswith(keccak(text="20261006-ab12cd34").hex()))
        self.assertEqual(gf_chain.splitter_idea_hash("20261006-ab12cd34"), "0x" + keccak(text="20261006-ab12cd34").hex())

    def test_decode_distributed(self):
        data = "0x" + f"{10 ** 18:064x}" + f"{2 * 10 ** 18:064x}" + f"{5000 * 10 ** 18:064x}"
        d = gf_chain.decode_distributed({"data": data, "blockNumber": "0x64", "logIndex": "0x2", "transactionHash": "0xABC"})
        self.assertEqual((d["creator_wei"], d["burn_wei"], d["epc_burned_wei"], d["block"], d["log_index"], d["tx_hash"]),
                         (10 ** 18, 2 * 10 ** 18, 5000 * 10 ** 18, 100, 2, "0xabc"))
        with self.assertRaises(ValueError):
            gf_chain.decode_distributed({"data": "0x01", "blockNumber": "0x1", "logIndex": "0x0", "transactionHash": "0x1"})


class FakeReader:
    def __init__(self):
        self.calls = []

    async def _call(self, method, params):
        self.calls.append((method, params))
        return {"eth_getTransactionCount": "0x5", "eth_gasPrice": "0x3b9aca00", "eth_estimateGas": "0x5208",
                "eth_sendRawTransaction": "0x" + "ab" * 32}[method]


class TestKeeperTransaction(unittest.TestCase):
    KEY = "0x" + "07" * 32

    def test_off_without_a_key(self):
        with mock.patch.object(settings, "GF_KEEPER_PRIVATE_KEY", ""):
            self.assertIsNone(asyncio.run(gf_chain.GfChain().send_distribute("0x" + "5b" * 20)))

    def test_signs_a_valid_transaction_for_distribute(self):
        reader = FakeReader()

        async def fake_reader():
            return reader
        with mock.patch.object(settings, "GF_KEEPER_PRIVATE_KEY", self.KEY), mock.patch.object(gf_chain.desk_chain, "reader", fake_reader):
            tx = asyncio.run(gf_chain.GfChain().send_distribute("0x" + "5b" * 20, 123))
        self.assertEqual(tx, "0x" + "ab" * 32)
        method, params = reader.calls[-1]
        self.assertEqual(method, "eth_sendRawTransaction")
        raw = bytes.fromhex(params[0][2:])
        decoded = __import__("eth_account").Account.recover_transaction(raw)
        self.assertEqual(decoded, Account.from_key(self.KEY).address)
        est = [c for c in reader.calls if c[0] == "eth_estimateGas"][0][1][0]
        self.assertEqual(est["data"], gf_chain.distribute_calldata(123))
        self.assertEqual(est["to"], "0x" + "5b" * 20)


class TestWalletAge(unittest.TestCase):
    def chain(self, post=None, get=None):
        class Client:
            async def post(self, url, json=None):
                return post(url, json)

            async def get(self, url, params=None):
                return get(url, params)

        class Resp:
            def __init__(self, body): self._b = body
            def raise_for_status(self): pass
            def json(self): return self._b

        c = gf_chain.GfChain(client=Client())
        c._Resp = Resp
        return c, Resp

    def test_alchemy_first_activity_takes_the_earliest_of_in_and_out(self):
        def post(url, payload):
            key = "fromAddress" if "fromAddress" in payload["params"][0] else "toAddress"
            ts = {"fromAddress": "2026-09-01T10:00:00.000Z", "toAddress": "2026-08-20T09:00:00.000Z"}[key]
            return R({"result": {"transfers": [{"metadata": {"blockTimestamp": ts}}]}})
        c, R = self.chain(post=lambda u, p: None)
        c._client.post = lambda url, json=None: _async(R({"result": {"transfers": [{"metadata": {"blockTimestamp": "2026-09-01T10:00:00.000Z" if "fromAddress" in json["params"][0] else "2026-08-20T09:00:00.000Z"}}]}}))
        with mock.patch.object(settings, "RH_MAINNET_RPC_URL", "https://alchemy.test/v2/key"):
            t = asyncio.run(c.first_activity(WALLET))
        self.assertEqual(t, datetime(2026, 8, 20, 9, tzinfo=timezone.utc))

    def test_the_result_is_cached_and_a_failure_is_not(self):
        calls = {"n": 0}
        c, R = self.chain()

        async def post(url, json=None):
            calls["n"] += 1
            return R({"result": {"transfers": [{"metadata": {"blockTimestamp": "2026-08-20T09:00:00.000Z"}}]}})
        c._client.post = post
        with mock.patch.object(settings, "RH_MAINNET_RPC_URL", "https://alchemy.test/v2/key"):
            a = asyncio.run(c.first_activity(WALLET))
            n = calls["n"]
            b = asyncio.run(c.first_activity(WALLET))
        self.assertEqual((a, b, calls["n"]), (a, a, n))
        # nothing found anywhere: None, and it is retried next time
        empty, R2 = self.chain()

        async def none_post(url, json=None):
            return R2({"result": {"transfers": []}})

        async def none_get(url, params=None):
            return R2({"items": []})
        empty._client.post, empty._client.get = none_post, none_get
        with mock.patch.object(settings, "RH_MAINNET_RPC_URL", "https://alchemy.test/v2/key"):
            self.assertIsNone(asyncio.run(empty.first_activity(WALLET)))
        self.assertNotIn(WALLET.lower(), empty._first_seen)

    def test_blockscout_fallback_and_total_failure(self):
        c, R = self.chain()

        async def get(url, params=None):
            return R({"items": [{"timestamp": "2026-09-10T00:00:00.000000Z"}, {"timestamp": "2026-08-01T00:00:00.000000Z"}]})
        c._client.get = get
        with mock.patch.object(settings, "RH_MAINNET_RPC_URL", ""):
            t = asyncio.run(c.first_activity(WALLET))
        self.assertEqual(t, datetime(2026, 8, 1, tzinfo=timezone.utc))

        broken, _ = self.chain()

        async def boom(*a, **k):
            raise RuntimeError("down")
        broken._client.get, broken._client.post = boom, boom
        with mock.patch.object(settings, "RH_MAINNET_RPC_URL", "https://alchemy.test/v2/key"):
            self.assertIsNone(asyncio.run(broken.first_activity(WALLET)))     # unknown: the vote is refused, not granted


async def _async(v):
    return v


# ---------------------------------------------------------------------------
# Launch planning
# ---------------------------------------------------------------------------

class TestLaunchTime(unittest.TestCase):
    def rates(self, **by_hour):
        return {str(h): r for h, r in by_hour.items()}

    def test_picks_the_best_hour_inside_the_window(self):
        rates = {str(h): 0.1 for h in range(24)}
        rates["3"] = 0.4
        rates["15"] = 0.9
        t, why = gf_launch.pick_launch_time(NOW, rates, 24)
        self.assertEqual(t, datetime(2026, 10, 7, 15, tzinfo=timezone.utc))
        self.assertIn("15:00 UTC", why)
        self.assertIn("90%", why)

    def test_the_slot_is_always_a_whole_hour_inside_the_window(self):
        for hours_rates in ({str(h): (h * 7 % 13) / 13 for h in range(24)}, {}, None):
            t, _ = gf_launch.pick_launch_time(NOW, hours_rates, 24)
            self.assertEqual((t.minute, t.second), (0, 0))
            self.assertGreaterEqual(t, NOW + timedelta(minutes=30))
            self.assertLessEqual(t, NOW + timedelta(hours=24))

    def test_ties_go_to_the_earliest_hour(self):
        t, _ = gf_launch.pick_launch_time(NOW, {str(h): 0.5 for h in range(24)}, 24)
        self.assertEqual(t, datetime(2026, 10, 6, 21, tzinfo=timezone.utc))

    def test_the_window_runs_from_the_announcement_to_24h_later(self):
        # 20:05 today + 24h = 20:05 tomorrow: 20:00 tomorrow is inside, 21:00 tomorrow is not
        t, _ = gf_launch.pick_launch_time(NOW, {"20": 0.99, "21": 0.2}, 24)
        self.assertEqual(t, datetime(2026, 10, 7, 20, tzinfo=timezone.utc))
        last = gf_launch.pick_launch_time(NOW, {"21": 0.9}, 24)[0]
        self.assertEqual(last, datetime(2026, 10, 6, 21, tzinfo=timezone.utc))      # tomorrow 21:00 is outside the window

    def test_no_data_says_so_and_takes_the_first_slot(self):
        for rates in (None, {}):
            t, why = gf_launch.pick_launch_time(NOW, rates, 24)
            self.assertEqual(t, datetime(2026, 10, 6, 21, tzinfo=timezone.utc))
            self.assertIn("No survival data", why)

    def test_integer_keys_and_missing_hours(self):
        t, _ = gf_launch.pick_launch_time(NOW, {4: 0.7, 9: 0.2}, 24)
        self.assertEqual(t.hour, 4)

    def test_lead_time_is_respected(self):
        t, _ = gf_launch.pick_launch_time(datetime(2026, 10, 6, 20, 40, tzinfo=timezone.utc), None, 24)
        self.assertEqual(t, datetime(2026, 10, 6, 21, 10, tzinfo=timezone.utc) if False else datetime(2026, 10, 6, 22, tzinfo=timezone.utc))

    def test_a_tiny_window_with_no_full_hour(self):
        t, why = gf_launch.pick_launch_time(NOW, None, 0)
        self.assertIn("No full hour", why)


class TestScoreboardHash(unittest.TestCase):
    ROWS = [{"rank": 1, "idea_id": "a", "name": "A", "ticker": "AA", "x_handle": "h", "credibility": 61.25, "golem": 55.5,
             "vote": 100.0, "final": 78.7, "votes": 12, "image_url": "/x", "winner": False, "detail": {"n": 1}}]

    def test_stable_and_order_of_keys_does_not_matter(self):
        a = gf_launch.scoreboard_hash(self.ROWS)
        shuffled = [dict(reversed(list(self.ROWS[0].items())))]
        self.assertEqual(a, gf_launch.scoreboard_hash(shuffled))
        self.assertEqual(len(a), 64)

    def test_changes_when_a_score_changes_but_not_for_unpublished_fields(self):
        base = gf_launch.scoreboard_hash(self.ROWS)
        self.assertNotEqual(base, gf_launch.scoreboard_hash([{**self.ROWS[0], "final": 78.71}]))
        self.assertEqual(base, gf_launch.scoreboard_hash([{**self.ROWS[0], "winner": True, "detail": {}, "image_url": "/y"}]))


class TestRegistration(unittest.TestCase):
    CA, TX, SP = "0x" + "Ca" * 20, "0x" + "AA" * 32, "0x" + "5B" * 20

    def test_valid_registration_is_lowercased(self):
        r = gf_launch.validate_registration(self.CA, self.TX, self.SP)
        self.assertEqual(r, {"ca": self.CA.lower(), "launch_tx": self.TX.lower(), "splitter_address": self.SP.lower()})
        self.assertIsNone(gf_launch.validate_registration(self.CA, self.TX, None)["splitter_address"])

    def test_bad_inputs(self):
        for args in (("0x12", self.TX, None), (self.CA, "0x12", None), (self.CA, self.TX, "xyz"), ("", self.TX, None), (None, self.TX, None)):
            with self.assertRaises(gf_launch.RegistrationError):
                gf_launch.validate_registration(*args)

    def test_backends(self):
        self.assertIsInstance(gf_launch.get_backend("manual"), gf_launch.ManualLaunchBackend)
        self.assertIsInstance(gf_launch.get_backend("anything-else"), gf_launch.ManualLaunchBackend)
        self.assertIsNone(asyncio.run(gf_launch.ManualLaunchBackend().launch({})))
        with self.assertRaises(gf_launch.LaunchBackendError):
            asyncio.run(gf_launch.get_backend("pons").launch({}))


# ---------------------------------------------------------------------------
# X posts
# ---------------------------------------------------------------------------

class TestPosts(unittest.TestCase):
    def winner(self, **kw):
        base = dict(round_date=date(2026, 10, 6), name="Spore Keeper", ticker="SPORE", handle="sporefan", final=72.45,
                    credibility=61.2, golem=70.1, vote=100.0, n_ideas=64, n_votes=1250)
        base.update(kw)
        return gf_poster.render_winner_post(**base)

    def test_winner_post_follows_the_template(self):
        with mock.patch.object(settings, "GF_X_MAX_CHARS", 4000):
            t = self.winner()
        self.assertEqual(t.splitlines()[0], "⚒️ GoForge winner, 2026-10-06")
        self.assertIn("Spore Keeper (SPORE)", t)
        self.assertIn("by @sporefan", t)
        self.assertIn("Score: 72.5 · Credibility 61.2 · Golem 70.1 · Votes 100.0", t)
        self.assertIn("64 ideas submitted · 1250 votes cast", t)
        self.assertIn("Golem launches it on Pons within 24h.", t)
        self.assertIn("Creator fees: 50% to the creator, 50% to EPC buyback & burn.", t)
        self.assertTrue(t.rstrip().endswith("Full scoreboard: epochlabs.run/goforge"))

    def test_no_dollar_sign_anywhere(self):
        self.assertNotIn("$", self.winner(name="$Spore", ticker="$SPORE"))
        self.assertNotIn("$", gf_poster.render_live_post(name="$A", ticker="$AA", ca="0x" + "1" * 40, pons_url=None, blockscout_url="https://b"))
        verdict = gf_poster.render_verdict_post(name="A", ticker="$AA", verdict="reached_30k", peak_mc_usd=41000, fees_to_creator_eth=1.5, epc_burned=5000)
        self.assertNotRegex(verdict, r"\$[A-Za-z]")      # no cashtag; "$41,000" is an amount and stays

    def test_the_handle_at_sign_is_not_doubled(self):
        self.assertIn("by @sporefan", self.winner(handle="@sporefan"))
        self.assertNotIn("@@", self.winner(handle="@sporefan"))

    def test_the_post_fits_x_even_with_the_longest_inputs(self):
        t = self.winner(name="N" * 32, ticker="T" * 10, handle="h" * 15, n_ideas=100, n_votes=999999)
        self.assertLessEqual(gf_poster.x_weight(t), 280)
        self.assertIn("epochlabs.run/goforge", t)        # the name is shortened, the link is never cut
        self.assertIn("by @" + "h" * 15, t)

    def test_the_brief_template_is_used_as_is_when_the_limit_allows_it(self):
        with mock.patch.object(settings, "GF_X_MAX_CHARS", 4000):
            t = self.winner()
        self.assertIn("Creator fees: 50% to the creator, 50% to EPC buyback & burn.", t)
        self.assertIn("Golem launches it on Pons within 24h.", t)

    def test_a_standard_account_gets_a_compact_post_that_still_has_everything_that_matters(self):
        t = self.winner()
        self.assertLessEqual(gf_poster.x_weight(t), 280)
        for needle in ("2026-10-06", "Spore Keeper (SPORE)", "@sporefan", "72.5", "Pons", "50%", "epochlabs.run/goforge"):
            self.assertIn(needle, t)

    def test_x_weight_counts_urls_and_emoji_the_way_x_does(self):
        self.assertEqual(gf_poster.x_weight("abc"), 3)
        self.assertEqual(gf_poster.x_weight("epochlabs.run/goforge"), 23)
        self.assertEqual(gf_poster.x_weight("https://example.com/very/long/path/that/goes/on"), 23)
        self.assertEqual(gf_poster.x_weight("⚒️"), 4)         # symbol + variation selector, 2 each
        self.assertEqual(gf_poster.x_weight("a · b"), 5)
        self.assertEqual(gf_poster.x_weight("…"), 2)

    def test_no_launch_post(self):
        t = gf_poster.render_no_launch_post(round_date=date(2026, 10, 6), reason_text=gf_poster.NO_LAUNCH_TEXT["no_votes"], n_ideas=3, n_votes=0)
        self.assertIn("no launch today", t)
        self.assertIn("No eligible vote was cast today.", t)

    def test_live_post_has_links_and_no_buy_call(self):
        ca = "0x" + "ab" * 20
        t = gf_poster.render_live_post(name="Spore", ticker="SPORE", ca=ca, pons_url="https://pons.test/x", blockscout_url="https://bs.test/token/x")
        for needle in (ca, "https://pons.test/x", "https://bs.test/token/x", "epochlabs.run/goforge"):
            self.assertIn(needle, t)
        for word in ("buy now", "ape", "moon", "pump"):
            self.assertNotIn(word, t.lower())
        self.assertNotIn("Pons:", gf_poster.render_live_post(name="Spore", ticker="SPORE", ca=ca, pons_url=None, blockscout_url="https://b"))

    def test_verdict_post(self):
        win = gf_poster.render_verdict_post(name="Spore", ticker="SPORE", verdict="reached_30k", peak_mc_usd=41200, fees_to_creator_eth=1.5, epc_burned=5000)
        lose = gf_poster.render_verdict_post(name="Spore", ticker="SPORE", verdict="stalled", peak_mc_usd=None, fees_to_creator_eth=0, epc_burned=0)
        self.assertIn("reached the 30K market cap", win)
        self.assertIn("$41,200", win)          # a dollar amount, not a ticker: it is followed by a digit
        self.assertIn("stalled below the 30K market cap", lose)
        self.assertIn("Peak market cap: n/a", lose)
        self.assertIn("Creator fees paid out: 1.5000 ETH", win)
        self.assertIn("EPC bought back and burned: 5,000", win)


if __name__ == "__main__":
    unittest.main()
