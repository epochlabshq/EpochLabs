import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest
from datetime import datetime, timedelta, timezone

from app.api.desk_endpoints import DeskInputs, build_desk_payload, find_trade
from app.core.config import settings
from app.services.desk import (
    Decision, anonymize_waiting, build_why, canonical_json, derive_state, gated_reason, group_trades,
    heartbeat, realized_pnl_wei, unrealized_pnl_wei, verify_why, watching_rows, why_hash,
)

E = 10 ** 18
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
TOKEN = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
SECRET = "0x" + "5e" * 20  # the queued candidate: must never appear before its entry confirms
MODEL = {"run_id": 42, "proven_floor": 0.61, "blocked_by": None}


def swap(tx, block, side, token, token_wei, eth_wei, minutes=0):
    return {"tx_hash": tx, "block": block, "at": NOW + timedelta(minutes=minutes), "side": side,
            "token": token, "token_amount_wei": token_wei, "eth_amount_wei": eth_wei}


def why(survival=0.74, run_id=42):
    return build_why(
        survival=survival, threshold=0.65,
        top_signals=[{"name": "launch_hour", "value": "14:00 UTC", "effect": "+", "contribution": 0.4},
                     {"name": "holders", "value": "1.2K", "effect": "+", "contribution": 0.3},
                     {"name": "lore", "value": '"community"', "effect": "+", "contribution": 0.1}],
        model_run_id=run_id, proven_floor=0.61, size_eth=0.1, size_rule="fixed 0.1 ETH",
        take_profit_mc_usd=30000, stop_loss_mc_usd=5000, max_hold_h=48, decided_at=NOW,
    )


def decision(id_, side, w=None, exit_reason=None):
    c = canonical_json(w) if w else None
    return Decision(id_, side, c, why_hash(c) if c else None, exit_reason)


def candidate(id_, token, stage, survival=0.7137, queued_min=0, dropped_h=None, reason=None):
    return {"id": id_, "token": token, "queued_at": NOW + timedelta(minutes=queued_min), "survival": survival,
            "stage": stage, "dropped_reason": reason,
            "dropped_at": NOW - timedelta(hours=dropped_h) if dropped_h is not None else None}


def watched(mint, name, symbol, survival):
    return {"mint": mint, "name": name, "symbol": symbol, "peak_mc": 15000, "launched_at": NOW - timedelta(hours=3),
            "holders": 300, "survival": survival}


def inputs(**kw):
    base = dict(epoch2_complete=True, paused_reason=None, model=MODEL,
                desk_state={"last_decision_at": NOW - timedelta(seconds=12)},
                watching_tokens=[], candidates=[], revealed=[], swaps=[], decisions={}, meta={}, burned_wei=0)
    base.update(kw)
    return DeskInputs(**base)


class TestCandidateAnonymity(unittest.TestCase):
    SECRET_NAME, SECRET_SYMBOL = "Zyzzyva Moon", "ZYZZ"

    def _payload(self, stage, **kw):
        inp = inputs(candidates=[candidate(3, SECRET, stage, **kw)],
                     meta={SECRET: {"name": self.SECRET_NAME, "symbol": self.SECRET_SYMBOL}})
        return build_desk_payload(inp, NOW)

    def _assert_no_identity(self, payload):
        blob = json.dumps(payload).lower()
        for s in (SECRET, SECRET[2:], self.SECRET_NAME.lower(), self.SECRET_SYMBOL.lower()):
            self.assertNotIn(s, blob)

    def test_no_identity_in_snapshot_at_any_pre_confirmation_stage(self):
        for stage in ("liquidity_check", "sizing", "entering"):
            payload = self._payload(stage)
            self._assert_no_identity(payload)
            self.assertEqual(payload["waiting"][0]["slot"], 3)
            self.assertEqual(payload["waiting"][0]["stage"], stage)

    def test_recently_dropped_stays_anonymous(self):
        payload = self._payload("dropped", dropped_h=1, reason="liquidity below minimum")
        self._assert_no_identity(payload)
        self.assertEqual(payload["waiting"][0]["dropped_reason"], "liquidity below minimum")

    def test_exact_survival_not_on_slot(self):
        # Watching publishes exact scores, so an exact score on a slot would identify the candidate
        payload = self._payload("sizing")
        self.assertNotIn("0.7137", json.dumps(payload["waiting"]))
        self.assertEqual(set(payload["waiting"][0]), {"slot", "stage", "queued_at"})

    def test_waiting_ws_event_body_is_the_snapshot_list(self):
        # desk_worker broadcasts {"desk_waiting": {"waiting": payload["waiting"]}}
        self._assert_no_identity({"desk_waiting": {"waiting": self._payload("entering")["waiting"]}})

    def test_watching_row_unchanged_when_token_is_queued(self):
        feed = [watched(SECRET, self.SECRET_NAME, self.SECRET_SYMBOL, 0.7137), watched(OTHER, "B", "B", 0.4)]
        before = build_desk_payload(inputs(watching_tokens=feed), NOW)
        after = build_desk_payload(inputs(watching_tokens=feed, candidates=[candidate(3, SECRET, "sizing")]), NOW)
        self.assertEqual(before["watching"], after["watching"])

    def test_no_trade_detail_for_a_candidate(self):
        inp = inputs(candidates=[candidate(3, SECRET, "entering")])
        self.assertIsNone(find_trade(inp, "t_0001"))
        self.assertIsNone(find_trade(inp, "3"))

    def test_dropped_candidate_revealed_only_after_delay(self):
        recent = anonymize_waiting([candidate(4, SECRET, "dropped", dropped_h=47, reason="x")], NOW, 72)
        self.assertNotIn(SECRET, json.dumps(recent))
        from app.services.desk import revealed_drops
        self.assertEqual(revealed_drops([candidate(4, SECRET, "dropped", dropped_h=47, reason="x")], NOW, 48), [])
        shown = revealed_drops([candidate(4, SECRET, "dropped", dropped_h=49, reason="x")], NOW, 48)
        self.assertEqual(shown[0]["token"], SECRET)

    def test_confirmed_entry_reveals_token(self):
        inp = inputs(swaps=[swap("0xb1", 10, "buy", SECRET, 1000 * E, E // 10)],
                     meta={SECRET: {"name": self.SECRET_NAME, "symbol": self.SECRET_SYMBOL}},
                     candidates=[candidate(3, SECRET, "open")])
        payload = build_desk_payload(inp, NOW)
        self.assertEqual(payload["open"][0]["token"]["symbol"], self.SECRET_SYMBOL)
        self.assertEqual(payload["waiting"], [])


class TestTrades(unittest.TestCase):
    def test_closed_win_matches_onchain_amounts(self):
        trades = group_trades([swap("0xb1", 10, "buy", TOKEN, 1000 * E, E // 10),
                               swap("0xs1", 20, "sell", TOKEN, 1000 * E, 15 * E // 100, minutes=90)])
        t = trades[0]
        self.assertTrue(t.closed)
        self.assertEqual(realized_pnl_wei(t), 5 * E // 100)
        row = build_desk_payload(inputs(swaps=[swap("0xb1", 10, "buy", TOKEN, 1000 * E, E // 10),
                                               swap("0xs1", 20, "sell", TOKEN, 1000 * E, 15 * E // 100, 90)]),
                                 NOW)["closed"][0]
        self.assertAlmostEqual(row["pnl"]["eth"], 0.05, places=12)
        self.assertAlmostEqual(row["pnl"]["pct"], 50.0, places=6)
        self.assertEqual(row["duration_s"], 90 * 60)

    def test_loss_is_negative_and_counted(self):
        swaps = [swap("0xb1", 10, "buy", TOKEN, 1000 * E, E // 10),
                 swap("0xs1", 20, "sell", TOKEN, 1000 * E, 4 * E // 100),
                 swap("0xb2", 30, "buy", OTHER, 500 * E, E // 10),
                 swap("0xs2", 40, "sell", OTHER, 500 * E, 12 * E // 100)]
        p = build_desk_payload(inputs(swaps=swaps), NOW)
        by_id = {r["id"]: r for r in p["closed"]}
        self.assertAlmostEqual(by_id["t_0001"]["pnl"]["eth"], -0.06, places=12)
        self.assertLess(by_id["t_0001"]["pnl"]["pct"], 0)
        self.assertEqual((p["pnl"]["wins"], p["pnl"]["losses"]), (1, 1))
        self.assertAlmostEqual(p["pnl"]["eth"], -0.04, places=12)
        self.assertAlmostEqual(p["pnl"]["pct"], -4.0, places=6)

    def test_partial_exits_use_average_cost(self):
        swaps = [swap("0xb1", 1, "buy", TOKEN, 600 * E, 6 * E // 100),
                 swap("0xb2", 2, "buy", TOKEN, 400 * E, 6 * E // 100),   # avg 0.00012 ETH/token, cost 0.12
                 swap("0xs1", 3, "sell", TOKEN, 500 * E, 9 * E // 100)]  # basis 0.06 -> +0.03 realized
        t = group_trades(swaps)[0]
        self.assertFalse(t.closed)
        self.assertEqual(realized_pnl_wei(t), 3 * E // 100)
        # 500 left valued at 0.0002 ETH/token = 0.1, remaining basis 0.06 -> +0.04 live
        self.assertEqual(unrealized_pnl_wei(t, (2 * E, 10_000 * E)), 4 * E // 100)

    def test_open_pnl_against_manual_within_tolerance(self):
        swaps = [swap("0xb1", 1, "buy", TOKEN, 123_456_789 * 10 ** 9, 87_654_321 * 10 ** 9)]
        mark = (7 * E, 9_000_000 * E)
        row = build_desk_payload(inputs(swaps=swaps, marks={TOKEN: mark}, decimals={TOKEN: 18}), NOW)["open"][0]
        manual = (123_456_789 * 10 ** 9) / E * (7 / 9_000_000) - 87_654_321 * 10 ** 9 / E
        self.assertLess(abs(row["pnl"]["eth"] - manual), abs(manual) * 0.001)
        self.assertAlmostEqual(row["mark_price"], 7 / 9_000_000, places=15)

    def test_open_without_mark_is_unknown_not_zero(self):
        p = build_desk_payload(inputs(swaps=[swap("0xb1", 1, "buy", TOKEN, 1000 * E, E // 10)]), NOW)
        self.assertIsNone(p["open"][0]["pnl"]["eth"])
        self.assertFalse(p["pnl"]["complete"])

    def test_ids_are_stable_and_reopen_is_a_new_trade(self):
        swaps = [swap("0xb1", 1, "buy", TOKEN, 100 * E, E // 10), swap("0xs1", 2, "sell", TOKEN, 100 * E, E // 10)]
        first = [t.id for t in group_trades(swaps)]
        swaps += [swap("0xb2", 3, "buy", TOKEN, 100 * E, E // 10)]
        later = group_trades(swaps)
        self.assertEqual([t.id for t in later][:1], first)
        self.assertEqual([(t.id, t.closed) for t in later], [("t_0001", True), ("t_0002", False)])

    def test_closed_trade_reports_whether_token_reached_30k(self):
        swaps = [swap("0xb1", 1, "buy", TOKEN, E, E // 10), swap("0xs1", 2, "sell", TOKEN, E, E // 5)]
        for status, expected in (("passed", True), ("stalled", False), ("pending", None)):
            row = build_desk_payload(inputs(swaps=swaps, meta={TOKEN: {"name": "A", "symbol": "A", "status": status}}),
                                     NOW)["closed"][0]
            self.assertIs(row["reached_30k"], expected)
            self.assertNotIn("status", row["token"])

    def test_sell_without_tracked_buy_is_ignored(self):
        self.assertEqual(group_trades([swap("0xs1", 1, "sell", TOKEN, 100 * E, E)]), [])

    def test_dust_counts_as_closed(self):
        t = group_trades([swap("0xb1", 1, "buy", TOKEN, 1000 * E, E),
                          swap("0xs1", 2, "sell", TOKEN, 1000 * E - 10 ** 15, E)])[0]
        self.assertTrue(t.closed)

    def test_no_default_filter_hides_losses(self):
        swaps = []
        for i in range(5):
            tok = "0x" + f"{i:02x}" * 20
            swaps += [swap(f"0xb{i}", 2 * i, "buy", tok, E, E // 10),
                      swap(f"0xs{i}", 2 * i + 1, "sell", tok, E, E // 20 if i % 2 else E // 5)]
        closed = build_desk_payload(inputs(swaps=swaps), NOW)["closed"]
        self.assertEqual(len(closed), 5)
        self.assertEqual(sum(1 for r in closed if r["pnl"]["eth"] < 0), 2)


class TestWhy(unittest.TestCase):
    def test_hash_matches_logged_hash(self):
        w = why()
        c = canonical_json(w)
        self.assertTrue(verify_why(c, why_hash(c)))
        self.assertEqual(canonical_json(json.loads(c)), c)

    def test_rewritten_why_fails_verification(self):
        c = canonical_json(why())
        h = why_hash(c)
        self.assertFalse(verify_why(c.replace("0.74", "0.84"), h))
        self.assertFalse(verify_why(None, h))

    def test_why_card_is_complete(self):
        w = why()
        self.assertEqual(set(w), {"survival", "threshold", "top_signals", "model_run_id", "proven_floor",
                                  "size_eth", "size_rule", "exit_plan", "decided_at"})
        self.assertEqual(len(w["top_signals"]), 3)
        self.assertEqual(set(w["exit_plan"]), {"take_profit_mc_usd", "stop_loss_mc_usd", "max_hold_h"})

    def test_entry_below_threshold_has_no_why(self):
        with self.assertRaises(ValueError):
            why(survival=0.5)

    def test_trade_detail_carries_verified_why(self):
        w = why()
        swaps = [swap("0xb1", 1, "buy", TOKEN, 1000 * E, E // 10), swap("0xs1", 2, "sell", TOKEN, 1000 * E, E // 5)]
        inp = inputs(swaps=swaps, decisions={"0xb1": decision(1, "buy", w),
                                             "0xs1": decision(2, "sell", exit_reason="take_profit")})
        d = find_trade(inp, "t_0001")
        self.assertEqual(d["status"], "closed")
        self.assertEqual(d["why"], w)
        self.assertTrue(d["why_verified"])
        self.assertEqual(d["exit_reason"], "take_profit")

    def test_tampered_stored_why_is_flagged(self):
        w = why()
        bad = Decision(1, "buy", canonical_json({**w, "survival": 0.99}), why_hash(canonical_json(w)), None)
        inp = inputs(swaps=[swap("0xb1", 1, "buy", TOKEN, 1000 * E, E // 10)], decisions={"0xb1": bad})
        self.assertFalse(build_desk_payload(inp, NOW)["open"][0]["why_verified"])


class TestStateAndHeartbeat(unittest.TestCase):
    def test_states(self):
        self.assertEqual(derive_state(False, None, 0, []), "gated")
        self.assertEqual(derive_state(True, "auc_std", 2, ["sizing"]), "paused")
        self.assertEqual(derive_state(True, None, 1, ["sizing"]), "in_position")
        self.assertEqual(derive_state(True, None, 0, ["sizing", "entering"]), "entering")
        self.assertEqual(derive_state(True, None, 0, ["liquidity_check"]), "waiting")
        self.assertEqual(derive_state(True, None, 0, []), "watching")

    def test_gated_payload_shows_blocking_gate(self):
        model = {"run_id": 7, "proven_floor": 0.31, "blocked_by": "n_samples"}
        p = build_desk_payload(inputs(epoch2_complete=False, model=model), NOW)
        self.assertEqual((p["state"], p["blocked_by"]), ("gated", "n_samples"))
        self.assertEqual(gated_reason({**model, "blocked_by": None}, 0.60), "proven_floor")
        self.assertEqual(gated_reason(None, 0.60), "no_model_run")

    def test_paused_keeps_open_positions_visible(self):
        p = build_desk_payload(inputs(paused_reason="auc_std",
                                      swaps=[swap("0xb1", 1, "buy", TOKEN, E, E // 10)]), NOW)
        self.assertEqual((p["state"], p["blocked_by"], len(p["open"])), ("paused", "auc_std", 1))

    def test_heartbeat_warns_after_five_minutes(self):
        self.assertFalse(heartbeat(NOW - timedelta(seconds=300), NOW, 300)["stale"])
        self.assertTrue(heartbeat(NOW - timedelta(seconds=301), NOW, 300)["stale"])
        self.assertTrue(heartbeat(None, NOW, 300)["stale"])


class TestWatching(unittest.TestCase):
    def test_sorted_by_survival_with_statuses(self):
        rows = watching_rows([watched(OTHER, "B", "B", 0.4), watched(TOKEN, "A", "A", 0.8),
                              watched("0x" + "cc" * 20, "C", "C", None)],
                             0.65, frozenset(), 50)
        self.assertEqual([(r["token"]["name"], r["status"]) for r in rows],
                         [("A", "scoring"), ("C", "unscored")])  # B is below the threshold: dropped

    def test_token_without_holders_awaits_holders(self):
        row = {**watched(TOKEN, "A", "A", None), "holders": None}
        self.assertEqual(watching_rows([row], 0.65, frozenset(), 50)[0]["status"], "awaiting_holders")

    def test_team_tokens_are_marked_excluded(self):
        epc = settings.EPOCH_TOKEN_CA
        rows = watching_rows([watched(epc, "EPC", "EPC", 0.9)], 0.65, settings.desk_excluded_tokens, 50)
        self.assertEqual(rows[0]["status"], "excluded")


if __name__ == "__main__":
    unittest.main()
