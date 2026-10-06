import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from PIL import Image

from app.core.config import settings
from app.services import gf_identity as ident
from app.services import gf_rounds as rounds
from app.services import gf_scoring as sc
from app.services import gf_validation as val

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
SCH = rounds.Schedule()
CHAIN = 4663
LORE = ("A tiny mushroom who remembers every holder by name and refuses to forget a single one of them, even "
        "when the market forgets the mushroom itself.")


def at(hour, minute=0, day=6):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Phase 1: schedule
# ---------------------------------------------------------------------------

class TestSchedule(unittest.TestCase):
    def test_phase_boundaries(self):
        cases = [(at(0), "submit"), (at(11, 59), "submit"), (at(12), "vote"), (at(19, 59), "vote"),
                 (at(20), "scoring"), (at(20, 4), "scoring"), (at(20, 5), "announced"), (at(23, 59), "announced")]
        for t, phase in cases:
            self.assertEqual(rounds.phase_at(t, SCH), phase, t)

    def test_round_date_follows_the_utc_day(self):
        self.assertEqual(rounds.round_date_at(at(0), SCH), date(2026, 10, 6))
        self.assertEqual(rounds.round_date_at(at(23, 59), SCH), date(2026, 10, 6))
        # a schedule that opens later in the day belongs to the previous round until then
        late = rounds.Schedule(open_hour=2)
        self.assertEqual(rounds.round_date_at(at(1), late), date(2026, 10, 5))

    def test_non_utc_input_is_converted(self):
        tz = timezone(timedelta(hours=7))
        self.assertEqual(rounds.phase_at(datetime(2026, 10, 6, 18, 59, tzinfo=tz), SCH), "submit")  # 11:59 UTC
        self.assertEqual(rounds.phase_at(datetime(2026, 10, 6, 19, 0, tzinfo=tz), SCH), "vote")     # 12:00 UTC

    def test_next_boundary_labels(self):
        self.assertEqual(rounds.next_boundary(at(3), SCH), ("submit_closes", at(12)))
        self.assertEqual(rounds.next_boundary(at(12), SCH), ("vote_closes", at(20)))
        self.assertEqual(rounds.next_boundary(at(20, 1), SCH), ("announcement", at(20, 5)))
        self.assertEqual(rounds.next_boundary(at(21), SCH), ("submit_opens", at(0, day=7)))

    def test_open_flags(self):
        self.assertTrue(rounds.submit_open(at(5), SCH))
        self.assertFalse(rounds.submit_open(at(12), SCH))
        self.assertTrue(rounds.vote_open(at(12), SCH))
        self.assertFalse(rounds.vote_open(at(20), SCH))

    def test_scoreboard_and_votes_hidden_until_the_vote_closes(self):
        d = date(2026, 10, 6)
        self.assertFalse(rounds.scoreboard_visible(at(19, 59), d, SCH))
        self.assertTrue(rounds.scoreboard_visible(at(20), d, SCH))
        self.assertFalse(rounds.votes_visible(at(15), d, SCH))
        self.assertTrue(rounds.votes_visible(at(20, 1), d, SCH))

    def test_schedule_comes_from_settings_and_is_validated(self):
        s = rounds.Schedule.from_settings(settings)
        self.assertEqual((s.close_hour, s.vote_close_hour, s.announce_minute), (12, 20, 5))
        with self.assertRaises(ValueError):
            rounds.Schedule(open_hour=12, close_hour=12, vote_close_hour=20)
        with self.assertRaises(ValueError):
            rounds.Schedule(announce_minute=60)

    def test_launch_deadline(self):
        self.assertEqual(rounds.launch_deadline(at(20, 5), SCH), at(20, 5, day=7))


# ---------------------------------------------------------------------------
# Phase 2: validation
# ---------------------------------------------------------------------------

def png(size=512, mode="RGB", fmt="PNG", dims=None):
    buf = io.BytesIO()
    Image.new(mode, dims or (size, size), (200, 120, 40)).save(buf, fmt)
    return buf.getvalue()


class TestFields(unittest.TestCase):
    def ok(self, **kw):
        base = dict(name="Spore Keeper", ticker="spore", lore=LORE)
        base.update(kw)
        return val.validate_fields(base["name"], base["ticker"], base["lore"])

    def codes(self, **kw):
        clean, problems = self.ok(**kw)
        self.assertIsNone(clean)
        return {(p.field, p.code) for p in problems}

    def test_valid_idea_is_cleaned_and_uppercased(self):
        clean, problems = self.ok(name="  Spore​   Keeper ")
        self.assertEqual(problems, [])
        self.assertEqual(clean["name"], "Spore Keeper")
        self.assertEqual(clean["ticker"], "SPORE")

    def test_name_length(self):
        self.assertIn(("name", "length"), self.codes(name="A"))
        self.assertIn(("name", "length"), self.codes(name="x" * 33))
        self.assertEqual(self.ok(name="Ab")[1], [])
        self.assertEqual(self.ok(name="x" * 32)[1], [])

    def test_ticker_rules(self):
        self.assertIn(("ticker", "length"), self.codes(ticker="A"))
        self.assertIn(("ticker", "length"), self.codes(ticker="ABCDEFGHIJK"))
        self.assertIn(("ticker", "charset"), self.codes(ticker="$SPORE"))
        self.assertIn(("ticker", "charset"), self.codes(ticker="SP-ORE"))
        self.assertIn(("ticker", "charset"), self.codes(ticker="SPÖRE"))
        self.assertEqual(self.ok(ticker="AB")[1], [])
        self.assertEqual(self.ok(ticker="A1B2C3D4E5")[1], [])

    def test_lore_length_bounds(self):
        self.assertIn(("lore", "length"), self.codes(lore="x" * 49))
        self.assertIn(("lore", "length"), self.codes(lore="word " * 250))  # 1,250 characters
        edge = ("sporelings gather at dawn to count the lights " * 4)[:50]
        self.assertEqual(self.ok(lore=edge)[1], [])
        varied = " ".join(f"word{i} lantern{i % 7} harbour{i % 11}" for i in range(80))[:1000]
        self.assertEqual(len(varied), 1000)
        self.assertEqual(self.ok(lore=varied)[1], [])

    def test_lore_safety_rules_are_reused(self):
        self.assertIn(("lore", "link"), self.codes(lore=LORE + " visit https://scam.example now"))
        self.assertIn(("lore", "link"), self.codes(lore=LORE + " see www.scam.example"))
        self.assertIn(("lore", "unsafe"), self.codes(lore=LORE + " you whore"))
        self.assertIn(("lore", "spam"), self.codes(lore=("buy buy buy " * 12)))
        self.assertIn(("lore", "too_few_words"), self.codes(lore="a" * 60))

    def test_name_cannot_hold_a_link(self):
        self.assertIn(("name", "link"), self.codes(name="visit www.x.io now"))

    def test_clean_text_drops_hidden_characters(self):
        self.assertEqual(val.clean_text("a​b‮c\x00d"), "abcd")


class TestImage(unittest.TestCase):
    def test_valid_formats(self):
        for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
            info, problems = val.validate_image(png(fmt=fmt))
            self.assertEqual(problems, [], fmt)
            self.assertEqual((info.ext, info.width, info.height), (ext, 512, 512))

    def test_hash_is_the_sha256_of_the_bytes(self):
        data = png()
        info, _ = val.validate_image(data)
        self.assertEqual(info.sha256, hashlib.sha256(data).hexdigest())

    def codes(self, data):
        info, problems = val.validate_image(data)
        self.assertIsNone(info)
        return problems[0].code

    def test_rejections(self):
        self.assertEqual(self.codes(b""), "missing")
        self.assertEqual(self.codes(png(dims=(600, 512))), "ratio")
        self.assertEqual(self.codes(png(size=256)), "too_small")
        self.assertEqual(self.codes(png(size=511)), "too_small")
        self.assertEqual(self.codes(b"not an image at all"), "unreadable")
        self.assertEqual(self.codes(png(fmt="GIF")), "format")
        self.assertEqual(self.codes(png(fmt="BMP")), "format")

    def test_size_limit(self):
        big = b"\x89PNG" + b"0" * (val.IMAGE_MAX_BYTES + 1)
        self.assertEqual(self.codes(big), "too_large")

    def test_truncated_file_is_unreadable(self):
        data = png(size=700)
        self.assertEqual(self.codes(data[: len(data) // 2]), "unreadable")

    def test_square_at_exactly_minimum_passes(self):
        self.assertEqual(val.validate_image(png(size=512))[1], [])


class TestBlocklists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bl = val.load_blocklists()

    def blocked(self, name, ticker="ZZZ"):
        return val.find_blocked_name(name, ticker, self.bl.names)

    def test_the_briefs_examples_are_rejected(self):
        self.assertIsNotNone(self.blocked("NVIDIA Coin", "NVC"))
        self.assertIsNotNone(self.blocked("Moon", "NVIDIA"))
        self.assertIsNotNone(self.blocked("Elon Doge", "EDOGE"))
        self.assertIsNotNone(self.blocked("Space Cat", "ELON"))

    def test_disguises_are_caught(self):
        self.assertIsNotNone(self.blocked("3L0N Cat"))
        self.assertIsNotNone(self.blocked("E-L-O-N"))
        self.assertIsNotNone(self.blocked("NVIDIAcoin"))
        self.assertIsNotNone(self.blocked("Teslaverse"))
        self.assertIsNotNone(self.blocked("x", "TESLAINU"))

    def test_clean_names_pass(self):
        for name, ticker in (("Spore Keeper", "SPORE"), ("Moon Mushroom", "MOSH"), ("Lantern Frog", "LFROG")):
            self.assertIsNone(self.blocked(name, ticker), name)

    def test_short_terms_only_match_a_whole_token(self):
        self.assertIsNotNone(self.blocked("IBM Cat"))
        self.assertIsNone(self.blocked("Ibmaculate Cat"))   # contains 'ibm' but is a different word
        self.assertIsNone(self.blocked("Amdahl", "AMDAHL"))

    def test_extra_terms_from_env(self):
        bl = val.load_blocklists(extra_names="Fooco, Barcorp", extra_tickers="zzzz")
        self.assertIsNotNone(val.find_blocked_name("Fooco Coin", "FC", bl.names))
        self.assertIn("ZZZZ", bl.tickers)

    def test_missing_files_give_empty_lists(self):
        with tempfile.TemporaryDirectory() as d:
            bl = val.load_blocklists(Path(d))
            self.assertEqual((len(bl.names), len(bl.tickers)), (0, 0))

    def test_ticker_collision(self):
        self.assertEqual(val.find_ticker_collision("NVDA", self.bl.tickers, []), "stock")
        self.assertEqual(val.find_ticker_collision("epc", self.bl.tickers, []), "stock")  # EPC vs Edgewell, the known case
        self.assertEqual(val.find_ticker_collision("MOSH", self.bl.tickers, ["mosh", None]), "existing")
        self.assertIsNone(val.find_ticker_collision("MOSH", self.bl.tickers, ["OTHER"]))


class TestDuplicates(unittest.TestCase):
    def test_cosine(self):
        self.assertAlmostEqual(val.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(val.cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(val.cosine([0, 0], [1, 1]), 0.0)

    def test_hash_embedding_is_deterministic_and_normalised(self):
        a, b = val.hash_embedding(LORE), val.hash_embedding(LORE)
        self.assertEqual(a, b)
        self.assertAlmostEqual(sum(x * x for x in a), 1.0)
        self.assertGreaterEqual(val.cosine(a, val.hash_embedding(LORE + "!")), 0.95)
        self.assertLess(val.cosine(a, val.hash_embedding("Completely different text about lighthouses and rain.")), 0.6)

    def test_duplicate_by_ticker_name_or_lore(self):
        vec = val.hash_embedding(LORE)
        others = [val.OtherIdea("i1", "Spore Keeper", "SPORE", vec)]
        far = val.hash_embedding("Lighthouses sing to ships that forgot the way home across a rainy grey sea.")
        self.assertEqual(val.find_duplicate("Other", "spore", far, others, 0.95), ("i1", "ticker"))
        self.assertEqual(val.find_duplicate("spore  keeper", "ZZZ", far, others, 0.95), ("i1", "name"))
        self.assertEqual(val.find_duplicate("Other", "ZZZ", vec, others, 0.95), ("i1", "lore"))
        self.assertIsNone(val.find_duplicate("Other", "ZZZ", far, others, 0.95))
        self.assertIsNone(val.find_duplicate("Other", "ZZZ", None, others, 0.95))

    def test_threshold_is_configurable(self):
        a = [1.0, 0.0]
        b = [0.9, 0.436]  # cosine ~0.9
        others = [val.OtherIdea("i1", "A", "AA", b)]
        self.assertIsNone(val.find_duplicate("B", "BB", a, others, 0.95))
        self.assertEqual(val.find_duplicate("B", "BB", a, others, 0.85), ("i1", "lore"))


class TestAutoModeration(unittest.TestCase):
    bl = val.load_blocklists()

    def mod(self, name="Spore Keeper", ticker="SPORE", others=(), existing=(), image_moderator=None, emb=None):
        return val.auto_moderate(name=name, ticker=ticker, embedding=emb, others=others, existing_symbols=existing,
                                 blocklists=self.bl, threshold=0.95, image_bytes=b"x", image_moderator=image_moderator)

    def test_clean_idea_goes_to_manual_review_never_straight_to_approved(self):
        r = self.mod()
        self.assertEqual(r.status, "pending_review")
        self.assertIsNone(r.reason)

    def test_visual_check_is_flagged_when_no_classifier_is_installed(self):
        self.assertTrue(self.mod().flags["needs_visual_check"])
        self.assertFalse(self.mod(image_moderator=lambda b: []).flags["needs_visual_check"])

    def test_rejections_carry_a_reason_the_submitter_sees(self):
        for kw, text in ((dict(name="NVIDIA Coin"), "brand"), (dict(ticker="NVDA"), "stock"),
                         (dict(ticker="MOSH", existing=["MOSH"]), "already used")):
            r = self.mod(**kw)
            self.assertEqual(r.status, "rejected", kw)
            self.assertIn(text, r.reason.lower())

    def test_duplicate_of_another_idea_today_is_rejected(self):
        vec = val.hash_embedding(LORE)
        r = self.mod(name="Other", ticker="OTH", emb=vec, others=[val.OtherIdea("i1", "Spore Keeper", "SPORE", vec)])
        self.assertEqual((r.status, r.flags["duplicate_of"]), ("rejected", "i1"))

    def test_image_classifier_can_reject(self):
        r = self.mod(image_moderator=lambda b: ["nsfw"])
        self.assertEqual((r.status, r.flags["image_reasons"]), ("rejected", ["nsfw"]))

    def test_first_failing_check_wins(self):
        r = self.mod(name="Elon Cat", ticker="NVDA")
        self.assertIn("brand", r.reason.lower())


# ---------------------------------------------------------------------------
# Phase 6: scoring
# ---------------------------------------------------------------------------

class TestCredibility(unittest.TestCase):
    def test_verified(self):
        self.assertEqual((sc.score_verified(True), sc.score_verified(False), sc.score_verified(None)), (100, 0, 0))

    def test_account_age_caps_at_two_years(self):
        d = lambda days: NOW - timedelta(days=days)
        self.assertAlmostEqual(sc.score_account_age(d(365), NOW), 50.0, places=1)
        self.assertEqual(sc.score_account_age(d(730), NOW), 100.0)
        self.assertEqual(sc.score_account_age(d(5000), NOW), 100.0)
        self.assertEqual(sc.score_account_age(None, NOW), 0.0)
        self.assertEqual(sc.score_account_age(NOW + timedelta(days=3), NOW), 0.0)

    def test_followers_use_a_log_scale_with_100k_as_full_marks(self):
        self.assertEqual(sc.score_followers(0), 0.0)
        self.assertEqual(sc.score_followers(None), 0.0)
        self.assertAlmostEqual(sc.score_followers(99999), 100.0, places=3)
        self.assertEqual(sc.score_followers(10 ** 7), 100.0)
        self.assertAlmostEqual(sc.score_followers(999), 60.0, places=3)

    def test_track_record(self):
        self.assertEqual(sc.score_track_record(0, 0), 50)
        self.assertEqual(sc.score_track_record(1, 0), 75)
        self.assertEqual(sc.score_track_record(2, 0), 100)
        self.assertEqual(sc.score_track_record(5, 0), 100)   # capped
        self.assertEqual(sc.score_track_record(0, 1), 35)
        self.assertEqual(sc.score_track_record(0, 9), 0)      # floored
        self.assertEqual(sc.score_track_record(2, 1), 85)

    def test_credibility_is_the_mean_of_four_equal_parts(self):
        c = sc.Creator(x_verified=True, x_created_at=NOW - timedelta(days=730), x_followers=99999,
                       wins_reached_30k=2)
        self.assertAlmostEqual(sc.credibility(c, NOW)["score"], 100.0, places=2)
        new = sc.Creator()
        self.assertAlmostEqual(sc.credibility(new, NOW)["score"], 12.5)   # only the neutral 50 of track record / 4


class TestGolemScore(unittest.TestCase):
    CL = {"survival_rate": 0.30, "n_resolved": 80, "baseline_rate": 0.15}

    def test_narrative_lift_to_score(self):
        self.assertEqual(sc.narrative_score(self.CL), (100.0, "ok"))                               # lift 2
        self.assertEqual(sc.narrative_score({**self.CL, "survival_rate": 0.15}), (50.0, "ok"))     # lift 1
        self.assertEqual(sc.narrative_score({**self.CL, "survival_rate": 0.0}), (0.0, "ok"))
        self.assertEqual(sc.narrative_score({**self.CL, "survival_rate": 0.9})[0], 100.0)          # capped
        self.assertAlmostEqual(sc.narrative_score({**self.CL, "survival_rate": 0.075})[0], 25.0)

    def test_thin_missing_or_baselineless_clusters_are_neutral(self):
        self.assertEqual(sc.narrative_score({**self.CL, "n_resolved": 19}), (50.0, "thin_cluster"))
        self.assertEqual(sc.narrative_score({**self.CL, "n_resolved": 20})[1], "ok")
        self.assertEqual(sc.narrative_score(None), (50.0, "no_cluster"))
        self.assertEqual(sc.narrative_score({**self.CL, "baseline_rate": 0}), (50.0, "no_baseline"))
        self.assertEqual(sc.narrative_score({**self.CL, "survival_rate": None}), (50.0, "no_baseline"))

    def test_lore_quality_curve_has_a_cap(self):
        q = sc.lore_quality
        self.assertEqual((q(10), q(50)), (0.0, 0.0))
        self.assertAlmostEqual(q(150), 50.0)
        self.assertEqual((q(250), q(400), q(600)), (100.0, 100.0, 100.0))
        self.assertAlmostEqual(q(800), 85.0)
        self.assertEqual((q(1000), q(5000)), (70.0, 70.0))
        self.assertLess(q(1000), q(300))   # an essay does not beat a good lore

    def test_golem_blend_and_saturation_penalty(self):
        g = sc.golem_score(100.0, 300, saturated=False)
        self.assertAlmostEqual(g["score"], 100.0)
        s = sc.golem_score(100.0, 300, saturated=True)
        self.assertAlmostEqual(s["score"], 80.0)
        self.assertEqual(sc.golem_score(0.0, 10, saturated=True)["score"], 0.0)   # never negative
        self.assertAlmostEqual(sc.golem_score(50.0, 150, saturated=False)["score"], 50.0)

    def test_nearest_cluster_matches_and_refuses_far_ideas(self):
        clusters = [{"cluster_id": "c1", "centroid_vec": [1.0, 0.0]}, {"cluster_id": "c2", "centroid_vec": [0.0, 1.0]}]
        self.assertEqual(sc.nearest_cluster([0.9, 0.1], clusters)["cluster_id"], "c1")
        self.assertEqual(sc.nearest_cluster([0.1, 0.9], clusters)["cluster_id"], "c2")
        self.assertIsNone(sc.nearest_cluster([-1.0, -1.0], clusters))      # fits no narrative: neutral, not forced
        self.assertIsNone(sc.nearest_cluster(None, clusters))
        self.assertIsNone(sc.nearest_cluster([1.0, 0.0], [{"cluster_id": "x", "centroid_vec": None}]))
        self.assertIsNone(sc.nearest_cluster([0.0, 0.0], clusters))


def idea(i, votes, *, creator=None, cluster=None, lore_len=300, saturated=False, submitted_min=0, x=None, wallet=None):
    return sc.IdeaInput(f"i{i}", NOW + timedelta(minutes=submitted_min), creator or sc.Creator(), lore_len, cluster,
                        saturated, votes, x or f"x{i}", wallet or f"0x{i:040x}")


class TestVoteAndFinal(unittest.TestCase):
    def test_vote_is_relative_to_the_top_idea(self):
        self.assertEqual(sc.vote_score(10, 10), 100.0)
        self.assertEqual(sc.vote_score(5, 10), 50.0)
        self.assertEqual(sc.vote_score(0, 10), 0.0)
        self.assertEqual(sc.vote_score(3, 0), 0.0)

    def test_final_score_is_40_30_30(self):
        self.assertEqual(sc.final_score(100, 100, 100), 100.0)
        self.assertEqual(sc.final_score(0, 0, 0), 0.0)
        self.assertEqual(sc.final_score(100, 0, 0), 40.0)
        self.assertEqual(sc.final_score(0, 100, 0), 30.0)
        self.assertEqual(sc.final_score(0, 0, 100), 30.0)
        self.assertEqual(sc.final_score(50, 80, 20), 50.0)   # 20 + 24 + 6

    def test_published_components_reproduce_the_published_final(self):
        scored = sc.score_pool([idea(1, 7, creator=sc.Creator(x_verified=True, x_followers=1234, x_created_at=NOW - timedelta(days=400)),
                                     cluster={"survival_rate": 0.21, "n_resolved": 50, "baseline_rate": 0.15}, lore_len=333),
                                idea(2, 3, lore_len=120)], NOW)
        for s in scored:
            self.assertEqual(s.final, round(0.4 * s.credibility + 0.3 * s.golem + 0.3 * s.vote, 2))

    def test_score_pool_normalises_votes_inside_the_pool(self):
        a, b, c = sc.score_pool([idea(1, 40), idea(2, 20), idea(3, 0)], NOW)
        self.assertEqual((a.vote, b.vote, c.vote), (100.0, 50.0, 0.0))

    def test_empty_votes_pool_scores_zero_votes(self):
        self.assertEqual([s.vote for s in sc.score_pool([idea(1, 0), idea(2, 0)], NOW)], [0.0, 0.0])

    def test_detail_lists_every_subcomponent(self):
        s = sc.score_pool([idea(1, 1)], NOW)[0]
        self.assertEqual(set(s.detail["credibility"]), {"verified", "account_age", "followers", "track_record"})
        self.assertEqual(set(s.detail["golem"]), {"narrative", "lore_quality", "saturation_penalty"})


class TestWinner(unittest.TestCase):
    CLOSE = at(20)

    def scored(self, *rows):
        return [sc.ScoredIdea(r[0], NOW + timedelta(minutes=r[2]), 0, 0, 0, r[1], 1, r[3] if len(r) > 3 else f"x{r[0]}",
                              r[4] if len(r) > 4 else f"0x{r[0]}") for r in rows]

    def test_highest_score_wins(self):
        r = sc.pick_winner(self.scored(("a", 50, 0), ("b", 70, 1), ("c", 60, 2)), [], self.CLOSE, 7, 10)
        self.assertEqual(r.winner.idea_id, "b")

    def test_tie_goes_to_the_earlier_submission(self):
        r = sc.pick_winner(self.scored(("late", 70, 5), ("early", 70, 1), ("mid", 70, 3)), [], self.CLOSE, 7, 10)
        self.assertEqual(r.winner.idea_id, "early")

    def test_creator_who_won_within_seven_days_is_skipped_by_x_user_or_wallet(self):
        pool = self.scored(("a", 90, 0, "xa", "0xa"), ("b", 80, 1, "xb", "0xb"), ("c", 70, 2, "xc", "0xc"))
        recent = [sc.RecentWin("xa", None, self.CLOSE - timedelta(days=3))]
        r = sc.pick_winner(pool, recent, self.CLOSE, 7, 10)
        self.assertEqual(r.winner.idea_id, "b")
        self.assertEqual(r.skipped[0][0], "a")
        # the same person under a new X account but the same wallet is still blocked
        recent = [sc.RecentWin("old-x", "0xA", self.CLOSE - timedelta(days=1))]
        self.assertEqual(sc.pick_winner(pool, recent, self.CLOSE, 7, 10).winner.idea_id, "b")

    def test_win_older_than_the_window_does_not_block(self):
        pool = self.scored(("a", 90, 0, "xa", "0xa"), ("b", 80, 1))
        recent = [sc.RecentWin("xa", "0xa", self.CLOSE - timedelta(days=7, minutes=1))]
        self.assertEqual(sc.pick_winner(pool, recent, self.CLOSE, 7, 10).winner.idea_id, "a")

    def test_chained_skips_move_down_the_ranking(self):
        pool = self.scored(("a", 90, 0, "xa", "0xa"), ("b", 80, 1, "xb", "0xb"), ("c", 70, 2, "xc", "0xc"))
        recent = [sc.RecentWin("xa", None, self.CLOSE - timedelta(days=2)), sc.RecentWin(None, "0xb", self.CLOSE - timedelta(days=5))]
        self.assertEqual(sc.pick_winner(pool, recent, self.CLOSE, 7, 10).winner.idea_id, "c")

    def test_all_creators_blocked_means_no_launch(self):
        pool = self.scored(("a", 90, 0, "xa", "0xa"))
        r = sc.pick_winner(pool, [sc.RecentWin("xa", None, self.CLOSE - timedelta(days=1))], self.CLOSE, 7, 10)
        self.assertEqual((r.winner, r.no_launch_reason), (None, "all_creators_in_cooldown"))

    def test_no_launch_without_ideas_or_votes(self):
        self.assertEqual(sc.pick_winner([], [], self.CLOSE, 7, 0).no_launch_reason, "no_approved_ideas")
        self.assertEqual(sc.pick_winner(self.scored(("a", 90, 0)), [], self.CLOSE, 7, 0).no_launch_reason, "no_votes")


class TestVoteRules(unittest.TestCase):
    def test_eligibility_checks_balance_and_age_and_fails_closed(self):
        ok = dict(balance_epc=5000, min_epc=1000, first_tx_at=NOW - timedelta(days=30), now=NOW, min_age_days=7)
        self.assertIsNone(sc.vote_eligibility(**ok))
        self.assertEqual(sc.vote_eligibility(**{**ok, "balance_epc": 999.99}), "balance_too_low")
        self.assertIsNone(sc.vote_eligibility(**{**ok, "balance_epc": 1000}))
        self.assertEqual(sc.vote_eligibility(**{**ok, "balance_epc": None}), "balance_unavailable")
        self.assertEqual(sc.vote_eligibility(**{**ok, "first_tx_at": NOW - timedelta(days=6, hours=23)}), "wallet_too_new")
        self.assertIsNone(sc.vote_eligibility(**{**ok, "first_tx_at": NOW - timedelta(days=7)}))
        self.assertEqual(sc.vote_eligibility(**{**ok, "first_tx_at": None}), "wallet_age_unknown")

    def test_balance_recheck_at_close(self):
        self.assertTrue(sc.counts_at_close(1000, 1000))
        self.assertFalse(sc.counts_at_close(999, 1000))
        self.assertFalse(sc.counts_at_close(None, 1000))   # unknown never counts

    def test_creator_cannot_vote_for_their_own_idea(self):
        self.assertTrue(sc.is_self_vote("0xAbC", None, "0xabc", "x1"))
        self.assertTrue(sc.is_self_vote("0xdef", "x1", "0xabc", "x1"))     # different wallet, same X account
        self.assertFalse(sc.is_self_vote("0xdef", "x2", "0xabc", "x1"))
        self.assertFalse(sc.is_self_vote("0xdef", None, "0xabc", "x1"))


# ---------------------------------------------------------------------------
# Phase 3: identity
# ---------------------------------------------------------------------------

KEY = "0x" + "11" * 32
ACCT = Account.from_key(KEY)
OTHER = Account.from_key("0x" + "22" * 32)


def siwe(account=ACCT, nonce="abcdef1234567890", issued=NOW, domain="epochlabs.run", chain=CHAIN, address=None, **kw):
    msg = ident.build_siwe_message(domain=domain, address=address or account.address, uri="https://epochlabs.run/goforge",
                                   chain_id=chain, nonce=nonce, issued_at=issued, **kw)
    sig = Account.sign_message(encode_defunct(text=msg), KEY if account is ACCT else "0x" + "22" * 32).signature.hex()
    return msg, sig


class TestSiwe(unittest.TestCase):
    def verify(self, msg, sig, now=NOW, domain="epochlabs.run", chain=CHAIN):
        return ident.verify_siwe(msg, sig, expected_domain=domain, expected_chain_id=chain, now=now)

    def test_round_trip_recovers_the_wallet_from_the_signature(self):
        msg, sig = siwe()
        out = self.verify(msg, sig)
        self.assertEqual(out["address"], ACCT.address.lower())
        self.assertEqual(out["nonce"], "abcdef1234567890")

    def test_parse_returns_every_field(self):
        msg, _ = siwe(expires_at=NOW + timedelta(hours=1))
        p = ident.parse_siwe_message(msg)
        self.assertEqual((p["domain"], p["chain_id"], p["version"]), ("epochlabs.run", CHAIN, "1"))
        self.assertEqual(p["expires"], NOW + timedelta(hours=1))

    def test_claiming_someone_elses_address_fails(self):
        # the message names OTHER's address but ACCT signed it: typing an address is not enough
        msg, _ = siwe(address=OTHER.address)
        sig = Account.sign_message(encode_defunct(text=msg), KEY).signature.hex()
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)

    def test_tampered_message_fails(self):
        msg, sig = siwe()
        with self.assertRaises(ident.IdentityError):
            self.verify(msg.replace("Nonce: abcdef", "Nonce: 123456"), sig)

    def test_wrong_domain_chain_and_version(self):
        msg, sig = siwe(domain="evil.example")
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)
        msg, sig = siwe(chain=1)
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)
        msg, sig = siwe()
        with self.assertRaises(ident.IdentityError):
            self.verify(msg.replace("Version: 1", "Version: 2"), sig)

    def test_expired_and_future_messages(self):
        msg, sig = siwe(issued=NOW - timedelta(minutes=11))
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)
        msg, sig = siwe(issued=NOW + timedelta(minutes=5))
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)
        msg, sig = siwe(issued=NOW - timedelta(minutes=5), expires_at=NOW - timedelta(minutes=1))
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, sig)

    def test_garbage_input(self):
        for bad in ("hello", "", "x wants you to sign in\n0x1"):
            with self.assertRaises(ident.IdentityError):
                self.verify(bad, "0x" + "00" * 65)
        msg, _ = siwe()
        with self.assertRaises(ident.IdentityError):
            self.verify(msg, "0xnotasignature")

    def test_nonce_is_long_random_hex(self):
        a, b = ident.new_nonce(), ident.new_nonce()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{32}$")


class TestSession(unittest.TestCase):
    W = ACCT.address.lower()

    def test_round_trip(self):
        tok = ident.issue_session("secret", self.W, NOW, 24)
        self.assertEqual(ident.read_session("secret", tok), None if False else self.W) if False else None
        # PyJWT checks expiry against the real clock, so use a token that expires in the future
        future = datetime.now(timezone.utc)
        tok = ident.issue_session("secret", self.W, future, 24)
        self.assertEqual(ident.read_session("secret", tok), self.W)

    def test_rejections(self):
        now = datetime.now(timezone.utc)
        tok = ident.issue_session("secret", self.W, now, 24)
        self.assertIsNone(ident.read_session("other-secret", tok))
        self.assertIsNone(ident.read_session("secret", tok + "x"))
        self.assertIsNone(ident.read_session("secret", None))
        self.assertIsNone(ident.read_session("", tok))
        expired = ident.issue_session("secret", self.W, now - timedelta(hours=48), 24)
        self.assertIsNone(ident.read_session("secret", expired))

    def test_no_secret_means_no_session(self):
        with self.assertRaises(ident.IdentityError):
            ident.issue_session("", self.W, NOW, 24)

    def test_subject_must_be_a_wallet(self):
        import jwt
        bad = jwt.encode({"sub": "not-an-address", "exp": int(datetime.now(timezone.utc).timestamp()) + 60, "iss": "goforge"},
                         "secret", algorithm="HS256")
        self.assertIsNone(ident.read_session("secret", bad))

    def test_token_for_another_issuer_is_refused(self):
        import jwt
        other = jwt.encode({"sub": self.W, "exp": int(datetime.now(timezone.utc).timestamp()) + 60, "iss": "someone"},
                           "secret", algorithm="HS256")
        self.assertIsNone(ident.read_session("secret", other))


class TestXOAuth(unittest.TestCase):
    def test_pkce_challenge_is_s256_of_the_verifier(self):
        import base64
        v, c = ident.pkce_pair()
        self.assertGreaterEqual(len(v), 43)
        self.assertEqual(c, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode())
        self.assertNotEqual(ident.pkce_pair()[0], v)

    def test_authorize_url(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(ident.x_authorize_url("cid", "https://api.test/cb", "st4te", "chal"))
        q = parse_qs(u.query)
        self.assertEqual((u.netloc, q["client_id"], q["state"], q["code_challenge_method"]), ("x.com", ["cid"], ["st4te"], ["S256"]))
        self.assertEqual(q["redirect_uri"], ["https://api.test/cb"])

    def test_parse_user_takes_everything_from_the_api_payload(self):
        u = ident.parse_x_user({"data": {"id": "123", "username": "SporeFan", "verified": True,
                                         "created_at": "2019-04-02T10:00:00.000Z",
                                         "public_metrics": {"followers_count": 4200}}})
        self.assertEqual((u["x_user_id"], u["x_handle"], u["x_verified"], u["x_followers"]), ("123", "SporeFan", True, 4200))
        self.assertEqual(u["x_created_at"], datetime(2019, 4, 2, 10, tzinfo=timezone.utc))

    def test_missing_fields_stay_none(self):
        u = ident.parse_x_user({"data": {"id": 7}})
        self.assertEqual((u["x_user_id"], u["x_handle"], u["x_verified"], u["x_created_at"], u["x_followers"]), ("7", None, None, None, None))

    def test_no_account_is_an_error(self):
        for bad in ({}, {"data": {}}, None):
            with self.assertRaises(ident.IdentityError):
                ident.parse_x_user(bad)

    def test_exchange_and_fetch_with_a_fake_client(self):
        class Resp:
            def __init__(self, code, body): self.status_code, self._b = code, body
            def json(self): return self._b

        class Client:
            def __init__(self): self.calls = []
            async def post(self, url, **kw):
                self.calls.append(("post", url, kw))
                return Resp(200, {"access_token": "tok"})
            async def get(self, url, **kw):
                self.calls.append(("get", url, kw))
                return Resp(200, {"data": {"id": "9", "username": "u"}})

        c = Client()
        tok = asyncio.run(ident.x_exchange_code(c, client_id="cid", client_secret="sec", redirect_uri="r", code="c", verifier="v"))
        self.assertEqual(tok, "tok")
        self.assertEqual(c.calls[0][2]["data"]["code_verifier"], "v")
        self.assertEqual(c.calls[0][2]["auth"], ("cid", "sec"))
        user = asyncio.run(ident.x_fetch_user(c, "tok"))
        self.assertEqual(user["x_user_id"], "9")
        self.assertEqual(c.calls[1][2]["headers"]["authorization"], "Bearer tok")

    def test_x_failures_raise(self):
        class Resp:
            status_code = 401
            def json(self): return {}

        class Client:
            async def post(self, *a, **k): return Resp()
            async def get(self, *a, **k): return Resp()

        with self.assertRaises(ident.IdentityError):
            asyncio.run(ident.x_exchange_code(Client(), client_id="c", client_secret="", redirect_uri="r", code="c", verifier="v"))
        with self.assertRaises(ident.IdentityError):
            asyncio.run(ident.x_fetch_user(Client(), "t"))


class TestPairing(unittest.TestCase):
    def test_new_pair_links(self):
        self.assertEqual(ident.link_decision(None, None, "x1", "0xA")[0], "link")

    def test_same_pair_refreshes(self):
        row = {"x_user_id": "x1", "wallet": "0xa"}
        self.assertEqual(ident.link_decision(row, row, "x1", "0xA")[0], "refresh")

    def test_x_account_linked_to_another_wallet_is_refused(self):
        action, reason = ident.link_decision({"x_user_id": "x1", "wallet": "0xold"}, None, "x1", "0xnew")
        self.assertEqual(action, "refuse")
        self.assertIn("different wallet", reason)

    def test_wallet_linked_to_another_x_account_is_refused(self):
        action, reason = ident.link_decision(None, {"x_user_id": "x-other", "wallet": "0xa"}, "x1", "0xa")
        self.assertEqual(action, "refuse")
        self.assertIn("different X account", reason)

    def test_x_account_without_a_wallet_yet_can_link(self):
        self.assertEqual(ident.link_decision({"x_user_id": "x1", "wallet": None}, None, "x1", "0xa")[0], "link")

    def test_team_identities(self):
        self.assertTrue(ident.is_team_identity("0xAA", None, {"0xaa"}, set()))
        self.assertTrue(ident.is_team_identity("0xbb", "x9", set(), {"x9"}))
        self.assertFalse(ident.is_team_identity("0xbb", "x1", {"0xaa"}, {"x9"}))
        self.assertFalse(ident.is_team_identity(None, None, {"0xaa"}, {"x9"}))
        self.assertIn(settings.GOLEM_WALLET.lower(), settings.gf_team_wallets)
        self.assertIn(settings.GOLEM_AGENT.lower(), settings.gf_team_wallets)


class TestVoteSignature(unittest.TestCase):
    def sign(self, idea_id="i1", round_date="2026-10-06", signed_at=None, account=ACCT, key=KEY, voter=None):
        signed_at = signed_at or int(NOW.timestamp())
        typed = ident.vote_typed_data(round_date=round_date, idea_id=idea_id, voter=voter or account.address,
                                      signed_at=signed_at, chain_id=CHAIN)
        sig = Account.sign_message(encode_typed_data(full_message=typed), key).signature.hex()
        return typed, sig, signed_at

    def verify(self, sig, signed_at, idea_id="i1", voter=None, now=NOW, round_date="2026-10-06"):
        return ident.verify_vote(round_date=round_date, idea_id=idea_id, voter=voter or ACCT.address, signed_at=signed_at,
                                 chain_id=CHAIN, signature=sig, now=now)

    def test_round_trip(self):
        _, sig, t = self.sign()
        self.assertEqual(self.verify(sig, t), ACCT.address.lower())

    def test_signature_is_bound_to_the_idea_round_and_time(self):
        _, sig, t = self.sign()
        for kw in (dict(idea_id="i2"), dict(round_date="2026-10-07")):
            with self.assertRaises(ident.IdentityError, msg=str(kw)):
                self.verify(sig, t, **kw)
        with self.assertRaises(ident.IdentityError):
            self.verify(sig, t + 1)

    def test_cannot_vote_as_someone_else(self):
        _, sig, t = self.sign(account=OTHER, key="0x" + "22" * 32, voter=ACCT.address)   # OTHER signs for ACCT
        with self.assertRaises(ident.IdentityError):
            self.verify(sig, t)

    def test_stale_signature_is_refused(self):
        _, sig, t = self.sign(signed_at=int(NOW.timestamp()) - 3600)
        with self.assertRaises(ident.IdentityError):
            self.verify(sig, t)
        _, sig, t = self.sign(signed_at=int(NOW.timestamp()) + 3600)
        with self.assertRaises(ident.IdentityError):
            self.verify(sig, t)

    def test_garbage_signature(self):
        with self.assertRaises(ident.IdentityError):
            self.verify("0x1234", int(NOW.timestamp()))

    def test_typed_data_shape_matches_what_a_wallet_signs(self):
        typed = ident.vote_typed_data(round_date="2026-10-06", idea_id="i1", voter=ACCT.address, signed_at=1, chain_id=CHAIN)
        self.assertEqual(typed["primaryType"], "Vote")
        self.assertEqual([f["name"] for f in typed["types"]["Vote"]], ["round", "ideaId", "voter", "signedAt"])
        self.assertEqual(typed["domain"], {"name": "EpochLabs GoForge", "version": "1", "chainId": CHAIN})
        json.dumps(typed)   # must be JSON-serialisable: the browser sends it to eth_signTypedData_v4


if __name__ == "__main__":
    unittest.main()
