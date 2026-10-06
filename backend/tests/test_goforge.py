import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json

import httpx
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.core import goforge_config
from app.core.config import settings
from app.core.goforge_config import GoForgeConfigError, LaunchEntry, goforge_cas, load_launches, parse_entries
from app.services import goforge_worker
from app.services.desk_trading import select_candidates
from app.services.goforge import (
    decide_verdict, holders_concentration, in_window, next_peak, seconds_to_verdict,
    serialize_history, serialize_launch, share_text, strip_cashtag, totals,
)
from app.services.goforge_sources import (
    DexResult, Pair, parse_blockscout_token, parse_dex_pairs, parse_timestamp, sum_burn_transfers,
)
from app.services.goforge_worker import Observation, plan_refresh

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
CA = "0x" + "ab" * 20
CA2 = "0x" + "cd" * 20
TX = "0x" + "12" * 32
ROUTER = "0x" + "fe" * 20
HOURS, TARGET = 48, 30000.0


def entry_json(**kw):
    base = {"id": "forge-001", "ca": CA, "launch_tx": TX}
    base.update(kw)
    return base


def entry(**kw):
    return parse_entries([entry_json(**kw)])[0]


def row(**kw):
    base = {"id": "forge-001", "ca": CA, "name": "Spore", "symbol": "SPR", "launch_tx": TX,
            "launched_at": NOW - timedelta(hours=6), "peak_mc_usd": 0, "verdict": "pending", "verdict_at": None,
            "fees_usd": 0, "epc_burned": 0, "pool_active_at": NOW - timedelta(hours=6), "pair_url": None,
            "decimals": 18, "total_supply": 1_000_000_000, "top10_pct": None,
            "last_source_ok_at": NOW - timedelta(seconds=30), "launched": True}
    base.update(kw)
    return base


def pair(mc=12000.0, liq=8000.0, **kw):
    return Pair(mc_usd=mc, liquidity_usd=liq, price_usd=0.0001, volume_24h_usd=900.0,
                pair_url="https://dexscreener.com/robinhood/0xpair", name="Spore", symbol="SPR", **kw)


def card(r=None, latest=None, e=None, now=NOW, **kw):
    return serialize_launch(
        r or row(), latest, e or entry(), now, blockscout="https://bs.test", dex_chain="robinhood", hours=HOURS,
        stale_after_s=180, settled_stale_after_s=1200, public_url="https://epochlabs.run/goforge", **kw)


class TestConfig(unittest.TestCase):
    def test_minimal_entry_lowercases_and_nulls_the_rest(self):
        e = parse_entries([entry_json(ca=CA.upper().replace("0X", "0x"))])[0]
        self.assertEqual(e.ca, CA)
        self.assertEqual(e.launch_tx, TX)
        self.assertTrue(all(v is None for v in e.rules.values()))
        self.assertTrue(all(v is None for v in e.why.values()))
        self.assertIsNone(e.fee_router_address)

    def test_required_fields_and_formats(self):
        for bad in ({"id": ""}, {"ca": None}, {"ca": "0x123"}, {"launch_tx": "0xabc"}, {"launch_tx": None}):
            with self.assertRaises(GoForgeConfigError, msg=str(bad)):
                parse_entries([entry_json(**bad)])
        with self.assertRaises(GoForgeConfigError):
            parse_entries([entry_json(rules={"hook_address": "nope"})])
        with self.assertRaises(GoForgeConfigError):
            parse_entries({"not": "a list"})

    def test_duplicates_rejected(self):
        with self.assertRaises(GoForgeConfigError):
            parse_entries([entry_json(), entry_json(ca=CA2)])
        with self.assertRaises(GoForgeConfigError):
            parse_entries([entry_json(), entry_json(id="forge-002")])

    def test_why_and_rules_are_kept(self):
        e = entry(why={"window": "Thu 14:00 UTC", "why_hash": "0xabc", "extra": "dropped"},
                  rules={"anti_snipe_blocks": 5, "hook_address": ROUTER}, fee_router_address=ROUTER)
        self.assertEqual(e.why["window"], "Thu 14:00 UTC")
        self.assertNotIn("extra", e.why)
        self.assertEqual(e.rules["anti_snipe_blocks"], 5)
        self.assertEqual(e.rules["hook_address"], ROUTER)
        self.assertEqual(e.fee_router_address, ROUTER)

    def test_file_loading_and_reload_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "launches.json"
            self.assertEqual(load_launches(p), [])  # missing file: no launches
            p.write_text("[]", encoding="utf-8")
            self.assertEqual(load_launches(p), [])
            p.write_text(json.dumps([entry_json()]), encoding="utf-8")
            os.utime(p, ns=(1, 2 ** 60))  # force a distinct mtime
            self.assertEqual([e.id for e in load_launches(p)], ["forge-001"])

    def test_repo_config_is_valid(self):
        self.assertIsInstance(load_launches(goforge_config.DEFAULT_PATH), list)


class TestTradingExclusion(unittest.TestCase):
    def _with_config(self, entries):
        d = tempfile.TemporaryDirectory()
        p = Path(d.name) / "launches.json"
        p.write_text(json.dumps(entries), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"GOFORGE_LAUNCHES_PATH": str(p)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(d.cleanup)

    def test_every_goforge_ca_is_excluded_from_trading(self):
        self._with_config([entry_json(), entry_json(id="forge-002", ca=CA2)])
        excluded = settings.desk_excluded_tokens
        self.assertTrue({CA, CA2} <= excluded)
        # the pre-existing exclusions are untouched
        self.assertIn(settings.EPOCH_TOKEN_CA.lower(), excluded)

    def test_executor_selection_never_picks_a_goforge_token(self):
        self._with_config([entry_json()])
        scored = [{"mint": CA.upper().replace("0X", "0x"), "survival": 0.99}, {"mint": "0x" + "11" * 20, "survival": 0.7}]
        picks = select_candidates(scored, threshold=0.65, excluded=settings.desk_excluded_tokens, busy=set(),
                                  open_count=0, queued_count=0, max_open=3)
        self.assertEqual([p["mint"] for p in picks], ["0x" + "11" * 20])

    def test_entry_is_excluded_before_its_pool_exists(self):
        # The exclusion comes from the config file alone, so it holds from the moment the CA is added
        self._with_config([entry_json()])
        self.assertIn(CA, goforge_cas())

    def test_wallet_sync_skips_goforge_tokens(self):
        from app.services import desk_wallet_sync
        self._with_config([entry_json()])
        other = "0x" + "77" * 20
        transfers = [
            {"hash": "0x" + "a1" * 32, "rawContract": {"address": CA}, "asset": "SPR", "blockNum": "0x10",
             "value": 5.0, "metadata": {"blockTimestamp": "2026-10-05T14:00:00Z"}},
            {"hash": "0x" + "a2" * 32, "rawContract": {"address": other}, "asset": "OTH", "blockNum": "0x11",
             "value": 5.0, "metadata": {"blockTimestamp": "2026-10-05T14:01:00Z"}},
        ]

        def Resp(body):
            return httpx.Response(200, json=body)

        async def post(url, json=None):
            if json["method"] == "alchemy_getAssetTransfers" and json["params"][0].get("toAddress"):
                return Resp({"result": {"transfers": transfers}})
            if json["method"] == "alchemy_getAssetTransfers":
                return Resp({"result": {"transfers": []}})
            return Resp({"result": {"value": "0x0", "logs": []}})

        client = mock.MagicMock()
        client.post = post
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        inserted = []

        async def execute(stmt, params=None):
            sql = str(stmt)
            if "INSERT INTO golem_swaps" in sql:
                inserted.append(params["tk"])
            res = mock.MagicMock()
            res.scalars.return_value.all.return_value = []
            res.scalar.return_value = 0
            return res

        db = mock.MagicMock()
        db.execute = execute
        db.commit = mock.AsyncMock()
        with mock.patch.object(desk_wallet_sync.httpx, "AsyncClient", return_value=client):
            asyncio.run(desk_wallet_sync.sync_wallet_transfers(db))
        self.assertEqual(inserted, [other])


class TestVerdict(unittest.TestCase):
    L = NOW

    def test_pending_until_hour_48(self):
        just_before = self.L + timedelta(hours=48) - timedelta(seconds=1)
        self.assertIsNone(decide_verdict("pending", self.L, just_before, 99999, hours=HOURS, target_usd=TARGET))

    def test_locks_exactly_at_hour_48(self):
        at = self.L + timedelta(hours=48)
        self.assertEqual(decide_verdict("pending", self.L, at, 30000, hours=HOURS, target_usd=TARGET), "reached_30k")
        self.assertEqual(decide_verdict("pending", self.L, at, 29999.99, hours=HOURS, target_usd=TARGET), "stalled")
        self.assertEqual(decide_verdict("pending", self.L, at, 0, hours=HOURS, target_usd=TARGET), "stalled")
        self.assertEqual(decide_verdict("pending", self.L, at, None, hours=HOURS, target_usd=TARGET), "stalled")

    def test_locked_verdict_never_changes(self):
        late = self.L + timedelta(days=30)
        for v in ("reached_30k", "stalled"):
            for peak in (0, 29999, 30000, 10 ** 9):
                self.assertIsNone(decide_verdict(v, self.L, late, peak, hours=HOURS, target_usd=TARGET))

    def test_peak_only_counts_snapshots_inside_the_window(self):
        self.assertTrue(in_window(self.L, self.L, HOURS))
        self.assertTrue(in_window(self.L + timedelta(hours=48), self.L, HOURS))
        self.assertFalse(in_window(self.L + timedelta(hours=48, seconds=1), self.L, HOURS))
        self.assertFalse(in_window(self.L - timedelta(seconds=1), self.L, HOURS))
        self.assertEqual(next_peak(10000, 25000, self.L + timedelta(hours=1), self.L, HOURS, "pending"), 25000)
        self.assertEqual(next_peak(25000, 12000, self.L + timedelta(hours=2), self.L, HOURS, "pending"), 25000)
        # a spike after the window must not flip stalled into reached
        self.assertEqual(next_peak(25000, 90000, self.L + timedelta(hours=50), self.L, HOURS, "pending"), 25000)

    def test_peak_is_frozen_once_locked(self):
        self.assertEqual(next_peak(25000, 90000, self.L + timedelta(hours=1), self.L, HOURS, "stalled"), 25000)

    def test_peak_comes_from_stored_snapshots_not_current_mc(self):
        # current MC has collapsed, the stored peak still decides
        self.assertEqual(next_peak(41000, 1000, self.L + timedelta(hours=3), self.L, HOURS, "pending"), 41000)
        self.assertEqual(decide_verdict("pending", self.L, self.L + timedelta(hours=48), 41000,
                                        hours=HOURS, target_usd=TARGET), "reached_30k")

    def test_missing_market_cap_keeps_peak(self):
        self.assertEqual(next_peak(5000, None, self.L + timedelta(hours=1), self.L, HOURS, "pending"), 5000)

    def test_countdown(self):
        self.assertEqual(seconds_to_verdict("pending", self.L, self.L + timedelta(hours=47), HOURS), 3600)
        self.assertEqual(seconds_to_verdict("pending", self.L, self.L + timedelta(hours=49), HOURS), 0)
        self.assertIsNone(seconds_to_verdict("stalled", self.L, self.L, HOURS))


class TestSerialization(unittest.TestCase):
    def test_not_public_before_the_pool_is_active(self):
        self.assertIsNone(card(r=row(pool_active_at=None)))

    def test_null_stays_null_never_zero_or_nan(self):
        c = card(latest=None)
        for k in ("price_usd", "mc_usd", "liquidity_usd", "volume_24h_usd", "holders", "top10_holder_pct"):
            self.assertIsNone(c[k], k)
        self.assertNotIn("NaN", json.dumps(c))
        self.assertNotIn("undefined", json.dumps(c))

    def test_peak_is_unknown_not_zero_without_any_market_read(self):
        self.assertIsNone(card(r=row(peak_mc_usd=0), latest=None)["peak_mc_usd"])
        self.assertEqual(card(r=row(peak_mc_usd=0), latest={"mc_usd": 5})["peak_mc_usd"], 0.0)
        self.assertEqual(card(r=row(peak_mc_usd=9000), latest=None)["peak_mc_usd"], 9000.0)

    def test_values_come_from_the_latest_snapshot(self):
        c = card(latest={"price_usd": 0.5, "mc_usd": 12345, "liquidity_usd": 800, "volume_24h_usd": 20, "holders": 77})
        self.assertEqual((c["mc_usd"], c["holders"], c["liquidity_usd"]), (12345.0, 77, 800.0))
        self.assertEqual(c["hours_since_launch"], 6.0)

    def test_stale_flag_and_age(self):
        fresh = card(r=row(last_source_ok_at=NOW - timedelta(seconds=60)))
        self.assertFalse(fresh["stale"])
        old = card(r=row(last_source_ok_at=NOW - timedelta(minutes=7)))
        self.assertTrue(old["stale"])
        self.assertEqual(old["stale_age_s"], 420)
        self.assertTrue(card(r=row(last_source_ok_at=None))["stale"])

    def test_settled_launches_use_the_longer_stale_limit(self):
        r = row(verdict="stalled", last_source_ok_at=NOW - timedelta(minutes=9))
        self.assertFalse(card(r=r)["stale"])  # refreshed every 10 min by design
        self.assertTrue(card(r=row(verdict="pending", last_source_ok_at=NOW - timedelta(minutes=9)))["stale"])

    def test_fees_and_burn_unknown_without_a_fee_router(self):
        c = card(r=row(fees_usd=0, epc_burned=0))
        self.assertIsNone(c["fees_usd"])
        self.assertIsNone(c["epc_burned"])
        c = card(r=row(fees_usd=12.5, epc_burned=300), e=entry(fee_router_address=ROUTER))
        self.assertEqual((c["fees_usd"], c["epc_burned"]), (12.5, 300.0))

    def test_rules_pending_flag(self):
        self.assertTrue(card()["rules_pending"])
        c = card(e=entry(rules={"anti_snipe_blocks": 3}))
        self.assertFalse(c["rules_pending"])
        self.assertEqual(c["rules"]["anti_snipe_blocks"], 3)
        self.assertIsNone(c["rules"]["hook_address"])

    def test_links(self):
        c = card(r=row(pair_url="https://dexscreener.com/robinhood/0xpair"))
        self.assertEqual(c["links"]["blockscout_token"], f"https://bs.test/token/{CA}")
        self.assertEqual(c["links"]["launch_tx"], f"https://bs.test/tx/{TX}")
        self.assertEqual(c["links"]["dexscreener"], "https://dexscreener.com/robinhood/0xpair")
        self.assertEqual(card()["links"]["dexscreener"], f"https://dexscreener.com/robinhood/{CA}")

    def test_stalled_card_is_a_full_card(self):
        c = card(r=row(verdict="stalled", verdict_at=NOW, peak_mc_usd=18000), latest={"mc_usd": 4000})
        self.assertEqual((c["verdict"], c["peak_mc_usd"], c["mc_usd"]), ("stalled", 18000.0, 4000.0))
        self.assertIsNone(c["seconds_to_verdict"])

    def test_why_is_echoed_not_rewritten(self):
        why = {"window": "Thu 14:00 UTC", "window_reason": "run 31", "lore_summary": "x", "model_run_id": "run_31",
               "why_hash": "0xdeadbeef"}
        self.assertEqual(card(e=entry(why=why))["why"], why)

    def test_no_buy_link_anywhere(self):
        blob = json.dumps(card()).lower()
        for word in ("buy", "swap", "trade now", "axiom"):
            self.assertNotIn(word, blob)

    def test_totals(self):
        cards = [card(r=row(id="a", verdict="reached_30k")), card(r=row(id="b", verdict="stalled")),
                 card(r=row(id="c", verdict="pending"))]
        t = totals(cards)
        self.assertEqual((t["launches"], t["reached_30k"], t["stalled"], t["pending"]), (3, 1, 1, 1))
        self.assertIsNone(t["fees_usd"])
        self.assertIsNone(t["epc_burned"])
        routed = [card(r=row(fees_usd=5, epc_burned=2), e=entry(fee_router_address=ROUTER)),
                  card(r=row(fees_usd=7, epc_burned=3), e=entry(fee_router_address=ROUTER))]
        t = totals(routed)
        self.assertEqual((t["fees_usd"], t["epc_burned"]), (12.0, 5.0))
        self.assertEqual(totals([])["launches"], 0)

    def test_history_is_windowed_and_skips_missing_mc(self):
        L = NOW
        snaps = [{"ts": L + timedelta(hours=h), "mc_usd": mc, "price_usd": None}
                 for h, mc in [(1, 100), (2, None), (3, 300), (49, 999)]]
        h = serialize_history(snaps, L, hours=48, target_usd=30000)
        self.assertEqual([p["mc_usd"] for p in h["points"]], [100.0, 300.0])
        self.assertEqual(h["target_mc_usd"], 30000)
        self.assertEqual(serialize_history(snaps, None, hours=48, target_usd=30000)["points"], [])


class TestShareText(unittest.TestCase):
    def test_ticker_is_written_without_a_dollar_sign(self):
        self.assertEqual(strip_cashtag("$SPORE"), "SPORE")
        self.assertEqual(strip_cashtag("Buy $SPORE and $EPC"), "Buy SPORE and EPC")
        self.assertEqual(strip_cashtag(None), "")
        for verdict in ("pending", "reached_30k", "stalled"):
            t = share_text("$Spore", "$SPR", verdict, "https://epochlabs.run/goforge")
            self.assertNotIn("$", t)
            self.assertIn("SPR", t)

    def test_card_symbol_has_no_dollar(self):
        c = card(r=row(symbol="$SPR", name="$Spore"))
        self.assertEqual(c["symbol"], "SPR")
        self.assertNotIn("$", c["share_text"])


class TestSources(unittest.TestCase):
    def test_dex_picks_the_deepest_pool_of_the_token(self):
        pairs = [
            {"baseToken": {"address": CA, "name": "Spore", "symbol": "SPR"}, "liquidity": {"usd": 100},
             "marketCap": 1000, "priceUsd": "0.1", "url": "u1"},
            {"baseToken": {"address": CA}, "liquidity": {"usd": 9000}, "marketCap": 5000, "priceUsd": "0.2",
             "volume": {"h24": 77}, "url": "u2"},
            {"baseToken": {"address": CA2}, "liquidity": {"usd": 10 ** 6}, "marketCap": 1, "url": "other"},
        ]
        p = parse_dex_pairs(pairs, CA.upper().replace("0X", "0x"))
        self.assertEqual((p.pair_url, p.liquidity_usd, p.mc_usd, p.volume_24h_usd), ("u2", 9000.0, 5000.0, 77.0))
        self.assertIsNone(parse_dex_pairs([], CA))
        self.assertIsNone(parse_dex_pairs([pairs[2]], CA))

    def test_dex_falls_back_to_fdv_and_keeps_missing_as_none(self):
        p = parse_dex_pairs([{"baseToken": {"address": CA}, "fdv": 4200}], CA)
        self.assertEqual(p.mc_usd, 4200.0)
        self.assertIsNone(p.price_usd)
        self.assertIsNone(p.volume_24h_usd)
        self.assertEqual(p.liquidity_usd, 0.0)

    def test_blockscout_token_normalizes_string_numbers(self):
        t = parse_blockscout_token({"name": "Spore", "symbol": "SPR", "decimals": "18",
                                    "total_supply": "1000000000000000000000", "holders_count": "321"})
        self.assertEqual((t["decimals"], t["holders"], t["total_supply"]), (18, 321, 10 ** 21))
        t = parse_blockscout_token({"holders": "9"})
        self.assertEqual(t["holders"], 9)
        self.assertIsNone(parse_blockscout_token({})["holders"])

    def test_timestamp(self):
        self.assertEqual(parse_timestamp("2026-10-05T14:00:12.000000Z"), datetime(2026, 10, 5, 14, 0, 12, tzinfo=timezone.utc))
        self.assertIsNone(parse_timestamp(None))
        self.assertIsNone(parse_timestamp("garbage"))

    def test_epc_burn_only_counts_router_to_dead_in_epc(self):
        epc = settings.EPOCH_TOKEN_CA
        dead = "0x000000000000000000000000000000000000dEaD"

        def t(frm, to, token, value, dec="18"):
            return {"from": {"hash": frm}, "to": {"hash": to}, "token": {"address_hash": token},
                    "total": {"value": value, "decimals": dec}}
        items = [t(ROUTER, dead, epc, str(5 * 10 ** 18)), t(ROUTER, dead, epc, str(10 ** 18)),
                 t(ROUTER, "0x" + "99" * 20, epc, str(10 ** 20)),   # not a burn
                 t("0x" + "88" * 20, dead, epc, str(10 ** 20)),     # not from the router
                 t(ROUTER, dead, CA, str(10 ** 20))]                # not EPC
        self.assertEqual(sum_burn_transfers(items, sender=ROUTER, epc=epc), 6.0)

    def test_top10_concentration(self):
        holders = [{"value": str(v)} for v in (400, 200, 100, 50, 50, 40, 30, 20, 10, 10, 5, 5)]
        self.assertEqual(holders_concentration(holders, 1000.0), 91.0)
        self.assertIsNone(holders_concentration([], 1000.0))
        self.assertIsNone(holders_concentration(holders, None))


class TestPlanRefresh(unittest.TestCase):
    def plan(self, r=None, obs=None, e=None, now=NOW):
        return plan_refresh(r or row(), e or entry(), obs or Observation(), now, hours=HOURS, target_usd=TARGET)

    def fresh_row(self, **kw):
        base = dict(pool_active_at=None, launched_at=None, name=None, symbol=None, decimals=None,
                    total_supply=None, last_source_ok_at=None)
        base.update(kw)
        return row(**base)

    def test_invisible_until_the_pool_has_liquidity(self):
        launch = {"launched_at": NOW, "deployer": "0x" + "01" * 20}
        # DexScreener answered: no pool
        p = self.plan(self.fresh_row(), Observation(dex=DexResult(True, None), launch=launch))
        self.assertNotIn("pool_active_at", p.updates)
        self.assertIsNone(p.snapshot)
        self.assertFalse(p.became_public)
        # a pair with zero liquidity is not an active pool
        p = self.plan(self.fresh_row(), Observation(dex=DexResult(True, pair(liq=0.0)), launch=launch))
        self.assertNotIn("pool_active_at", p.updates)
        self.assertIsNone(p.snapshot)

    def test_not_public_without_a_launch_time(self):
        p = self.plan(self.fresh_row(), Observation(dex=DexResult(True, pair()), launch=None))
        self.assertNotIn("pool_active_at", p.updates)
        self.assertIsNone(p.snapshot)

    def test_pool_goes_public_with_metadata_and_first_snapshot(self):
        obs = Observation(dex=DexResult(True, pair(mc=11000.0)), launch={"launched_at": NOW, "deployer": "0xd"},
                          token={"name": "Spore", "symbol": "SPR", "decimals": 18, "total_supply": 10 ** 9,
                                 "holders": 12}, holders=12)
        p = self.plan(self.fresh_row(), obs)
        self.assertTrue(p.became_public)
        self.assertEqual(p.updates["pool_active_at"], NOW)
        self.assertEqual(p.updates["name"], "Spore")
        self.assertEqual(p.updates["launched_at"], NOW)
        self.assertEqual(p.snapshot["mc_usd"], 11000.0)
        self.assertEqual(p.snapshot["holders"], 12)
        self.assertEqual(p.updates["peak_mc_usd"], 11000.0)
        self.assertEqual(p.updates["last_source_ok_at"], NOW)

    def test_peak_follows_stored_value_not_current(self):
        r = row(peak_mc_usd=25000)
        p = self.plan(r, Observation(dex=DexResult(True, pair(mc=9000.0))))
        self.assertNotIn("peak_mc_usd", p.updates)
        p = self.plan(r, Observation(dex=DexResult(True, pair(mc=26000.0))))
        self.assertEqual(p.updates["peak_mc_usd"], 26000.0)

    def test_failed_dex_read_writes_no_snapshot_and_no_zero(self):
        p = self.plan(row(), Observation(dex=DexResult(False)))
        self.assertIsNone(p.snapshot)
        self.assertNotIn("last_source_ok_at", p.updates)
        self.assertNotIn("peak_mc_usd", p.updates)

    def test_missing_holders_stay_none_in_the_snapshot(self):
        p = self.plan(row(), Observation(dex=DexResult(True, pair()), holders=None))
        self.assertIsNone(p.snapshot["holders"])

    def test_verdict_locks_at_hour_48_from_stored_peak(self):
        r = row(launched_at=NOW - timedelta(hours=48), pool_active_at=NOW - timedelta(hours=48), peak_mc_usd=31000)
        # DexScreener is down at the deadline: the verdict still locks
        p = self.plan(r, Observation(dex=DexResult(False)))
        self.assertEqual(p.verdict_locked, "reached_30k")
        self.assertEqual(p.updates["verdict"], "reached_30k")
        self.assertEqual(p.updates["verdict_at"], NOW)
        r = row(launched_at=NOW - timedelta(hours=48), pool_active_at=NOW - timedelta(hours=48), peak_mc_usd=29000)
        self.assertEqual(self.plan(r, Observation(dex=DexResult(False))).verdict_locked, "stalled")

    def test_snapshot_after_the_window_cannot_lift_the_peak(self):
        r = row(launched_at=NOW - timedelta(hours=48, minutes=1), pool_active_at=NOW - timedelta(hours=48),
                peak_mc_usd=10000)
        p = self.plan(r, Observation(dex=DexResult(True, pair(mc=500000.0))))
        self.assertEqual(p.verdict_locked, "stalled")
        self.assertNotIn("peak_mc_usd", p.updates)

    def test_locked_verdict_is_never_rewritten(self):
        r = row(launched_at=NOW - timedelta(days=9), verdict="stalled", verdict_at=NOW - timedelta(days=7),
                peak_mc_usd=12000)
        p = self.plan(r, Observation(dex=DexResult(True, pair(mc=900000.0))))
        self.assertIsNone(p.verdict_locked)
        for col in ("verdict", "verdict_at", "peak_mc_usd"):
            self.assertNotIn(col, p.updates)
        self.assertIsNotNone(p.snapshot)  # the live price still updates

    def test_no_verdict_before_the_pool_is_active(self):
        r = self.fresh_row(launched_at=NOW - timedelta(hours=60))
        p = self.plan(r, Observation(dex=DexResult(True, None)))
        self.assertIsNone(p.verdict_locked)

    def test_fee_router_burn_and_fees(self):
        e = entry(fee_router_address=ROUTER)
        r = row(epc_burned=10)
        p = self.plan(r, Observation(dex=DexResult(True, pair()), epc_burned=14.5, fees_eth=0.5, eth_usd=2000.0), e=e)
        self.assertEqual(p.updates["epc_burned"], 14.5)
        self.assertEqual(p.burn_delta, 4.5)
        self.assertEqual(p.updates["fees_usd"], 1000.0)
        # no new burn: no event
        p = self.plan(r, Observation(dex=DexResult(True, pair()), epc_burned=10.0), e=e)
        self.assertIsNone(p.burn_delta)
        # a failed burn read leaves the stored value alone
        p = self.plan(r, Observation(dex=DexResult(True, pair()), epc_burned=None, fees_eth=None), e=e)
        self.assertNotIn("epc_burned", p.updates)
        self.assertNotIn("fees_usd", p.updates)

    def test_without_a_fee_router_nothing_is_tracked(self):
        p = self.plan(row(), Observation(dex=DexResult(True, pair()), epc_burned=99.0, fees_eth=9.0, eth_usd=2000.0))
        self.assertNotIn("epc_burned", p.updates)
        self.assertNotIn("fees_usd", p.updates)

    def test_top_holder_concentration(self):
        holders = [{"value": "500"}, {"value": "250"}]
        p = self.plan(row(total_supply=1000), Observation(dex=DexResult(True, pair()), top_holders=holders))
        self.assertEqual(p.updates["top10_pct"], 75.0)

    def test_update_columns_are_whitelisted(self):
        obs = Observation(dex=DexResult(True, pair()), launch={"launched_at": NOW, "deployer": "0xd"},
                          token={"name": "n", "symbol": "s", "decimals": 1, "total_supply": 1, "holders": 1},
                          top_holders=[{"value": "1"}], epc_burned=1.0, fees_eth=1.0, eth_usd=1.0)
        p = self.plan(self.fresh_row(), obs, e=entry(fee_router_address=ROUTER))
        self.assertTrue(set(p.updates) <= goforge_worker.UPDATABLE)


class TestWorkerScheduling(unittest.TestCase):
    def setUp(self):
        goforge_worker._last_refresh.clear()
        goforge_worker._settled.clear()
        self.addCleanup(goforge_worker._last_refresh.clear)
        self.addCleanup(goforge_worker._settled.clear)

    def test_due_intervals(self):
        e1, e2 = entry(), entry(id="forge-002", ca=CA2)
        self.assertEqual(len(goforge_worker.due_entries([e1, e2], 1000.0)), 2)
        goforge_worker._last_refresh.update({"forge-001": 1000.0, "forge-002": 1000.0})
        goforge_worker._settled.add("forge-002")
        self.assertEqual(goforge_worker.due_entries([e1, e2], 1030.0), [])
        self.assertEqual([e.id for e in goforge_worker.due_entries([e1, e2], 1061.0)], ["forge-001"])
        self.assertEqual(len(goforge_worker.due_entries([e1, e2], 1000.0 + settings.GOFORGE_SETTLED_INTERVAL_SECONDS)), 2)

    def test_empty_registry_does_no_database_work(self):
        with mock.patch.object(goforge_worker, "all_entries", return_value=[]), \
             mock.patch.object(goforge_worker, "exclusive", side_effect=AssertionError("touched the DB")), \
             mock.patch.object(goforge_worker, "AsyncSessionLocal", side_effect=AssertionError("touched the DB")):
            asyncio.run(goforge_worker.run_goforge_cycle())

    def test_broken_config_skips_the_cycle(self):
        with mock.patch.object(goforge_worker, "all_entries", side_effect=GoForgeConfigError("bad")), \
             mock.patch.object(goforge_worker, "exclusive", side_effect=AssertionError("touched the DB")):
            asyncio.run(goforge_worker.run_goforge_cycle())


class TestPayloadLeaks(unittest.TestCase):
    """The REST payload and every WS event come from serialize_rows: an upcoming launch must be absent from both."""

    def test_unreleased_launch_is_absent_from_the_payload(self):
        from app.api import goforge_endpoints as api
        secret_ca = "0x" + "5e" * 20
        hidden = row(id="forge-002", ca=secret_ca, name="SecretName", symbol="SECRETSYM", pool_active_at=None)
        public = row()
        entries = {"forge-001": entry(), "forge-002": entry(id="forge-002", ca=secret_ca)}
        with mock.patch.object(api, "_entries_by_id", return_value=entries):
            payload = api.build_launches_payload([public, hidden], {}, NOW)
        blob = json.dumps(payload)
        for secret in (secret_ca, "SecretName", "SECRETSYM", "forge-002"):
            self.assertNotIn(secret, blob)
        self.assertEqual(payload["totals"]["launches"], 1)
        self.assertEqual([c["id"] for c in payload["launches"]], ["forge-001"])

    def test_empty_state(self):
        from app.api import goforge_endpoints as api
        with mock.patch.object(api, "_entries_by_id", return_value={}):
            payload = api.build_launches_payload([], {}, NOW)
        self.assertEqual(payload["launches"], [])
        self.assertEqual(payload["totals"]["launches"], 0)

    def test_newest_first_and_removed_config_entry_is_kept(self):
        from app.api import goforge_endpoints as api
        old = row(id="forge-001", launched_at=NOW - timedelta(days=9))
        new = row(id="forge-002", ca=CA2, launched_at=NOW - timedelta(hours=1))
        with mock.patch.object(api, "_entries_by_id", return_value={"forge-002": entry(id="forge-002", ca=CA2)}):
            out = api.serialize_rows([old, new], {}, NOW)
        self.assertEqual([c["id"] for c in out], ["forge-002", "forge-001"])  # forge-001 has no entry: still listed

    def test_history_endpoint_hides_unreleased_launches(self):
        from fastapi import HTTPException
        from app.api import goforge_endpoints as api

        class Result:
            def __init__(self, r): self._r = r
            def mappings(self): return self
            def first(self): return self._r
            def all(self): return []

        class Db:
            def __init__(self, r): self._r = r
            async def execute(self, *_a, **_k): return Result(self._r)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api.get_goforge_history("forge-002", Db(row(pool_active_at=None))))
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api.get_goforge_history("nope", Db(None)))
        self.assertEqual(ctx.exception.status_code, 404)
        out = asyncio.run(api.get_goforge_history("forge-001", Db(row())))
        self.assertEqual(out["points"], [])


class TestDeskWorkerRegression(unittest.TestCase):
    def test_desk_worker_imports_time(self):
        # run_desk_cycle calls time.time(): without the import every cycle died with a NameError
        from app.services import desk_worker
        self.assertTrue(hasattr(desk_worker, "time"))


if __name__ == "__main__":
    unittest.main()
