import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import gf_pg
from app.services import gf_store as store

D = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
W1, W2, W3 = ("0x" + c * 40 for c in "abc")


def run(coro_fn):
    async def go():
        await gf_pg.truncate_all()
        async with gf_pg.session() as db:
            return await coro_fn(db)
    return asyncio.run(go())


def X(i="x1", handle="spore"):
    return {"x_user_id": i, "x_handle": handle, "x_verified": True, "x_created_at": NOW - timedelta(days=900), "x_followers": 5000}


def idea(i="i1", wallet=W1, x="x1", ticker="SPORE", name="Spore", fee="0x" + "f1" * 32, status="approved", round_date=D, minute=0):
    return dict(idea_id=i, round_date=round_date, x_user_id=x, wallet=wallet, name=name, ticker=ticker, lore="lore " * 12,
                image_url="/thumbnails/goforge/a.png", image_sha256="a" * 64, fee_tx=fee, status=status, reject_reason=None,
                auto_flags={"needs_visual_check": True}, lore_embedding=[0.1, 0.2, 0.3], submitted_at=NOW + timedelta(minutes=minute))


async def seed(db, *ideas, creators=(("x1", "spore"), ("x2", "moss"), ("x3", "fern"))):
    await store.ensure_round(db, D)
    for x, h in creators:
        await store.upsert_creator(db, X(x, h), {"x1": W1, "x2": W2, "x3": W3}[x])
    for k, i in enumerate(ideas):
        await store.insert_idea(db, i)
    await db.commit()


class PgCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gf_pg.setup_schema()


class TestRoundsAndCreators(PgCase):
    def test_ensure_round_is_idempotent_and_starts_in_submit(self):
        async def go(db):
            a = await store.ensure_round(db, D)
            await store.set_round(db, D, status="vote")
            b = await store.ensure_round(db, D)     # must not reset the status
            return a["status"], b["status"]
        self.assertEqual(run(go), ("submit", "vote"))

    def test_set_round_only_accepts_known_columns(self):
        async def go(db):
            await store.ensure_round(db, D)
            with self.assertRaises(AssertionError):
                await store.set_round(db, D, not_a_column=1)
        run(go)

    def test_open_rounds_before(self):
        async def go(db):
            for d, st in ((date(2026, 10, 3), "vote"), (date(2026, 10, 4), "announced"), (date(2026, 10, 5), "submit")):
                await store.ensure_round(db, d)
                await store.set_round(db, d, status=st)
            await store.ensure_round(db, D)
            return [r["round_date"] for r in await store.open_rounds_before(db, D)]
        self.assertEqual(run(go), [date(2026, 10, 3), date(2026, 10, 5)])

    def test_creator_link_refresh_and_lookup(self):
        async def go(db):
            await store.upsert_creator(db, X("x1", "old_handle"), W1.upper().replace("0X", "0x"))
            await store.upsert_creator(db, {**X("x1", "new_handle"), "x_followers": 9999}, W1)
            c = await store.creator_by_x(db, "x1")
            return c["x_handle"], c["x_followers"], c["wallet"], (await store.creator_by_wallet(db, W1))["x_user_id"]
        self.assertEqual(run(go), ("new_handle", 9999, W1, "x1"))

    def test_one_wallet_cannot_belong_to_two_x_accounts(self):
        async def go(db):
            await store.upsert_creator(db, X("x1"), W1)
            with self.assertRaises(DBAPIError):
                await store.upsert_creator(db, X("x2"), W1)
        run(go)

    def test_wallet_change_is_logged(self):
        async def go(db):
            await store.upsert_creator(db, X("x1"), W1)
            await store.change_wallet(db, "x1", W2, "lost key, verified over DM", "team:alice")
            await db.commit()
            log = (await db.execute(text("SELECT old_wallet, new_wallet, reason, changed_by FROM gf_wallet_changes"))).all()
            return (await store.creator_by_x(db, "x1"))["wallet"], [tuple(r) for r in log]
        wallet, log = run(go)
        self.assertEqual(wallet, W2)
        self.assertEqual(log, [(W1, W2, "lost key, verified over DM", "team:alice")])

    def test_wins_and_track_record_counters(self):
        async def go(db):
            await store.upsert_creator(db, X("x1"), W1)
            await store.add_creator_result(db, "x1", "reached_30k")
            await store.add_creator_result(db, "x1", "reached_30k")
            await store.add_creator_result(db, "x1", "stalled")
            await store.set_last_win(db, "x1", NOW)
            c = await store.creator_by_x(db, "x1")
            return c["wins_reached_30k"], c["wins_stalled"], c["last_win_at"]
        self.assertEqual(run(go), (2, 1, NOW))


class TestIdeas(PgCase):
    def test_insert_count_and_lookups(self):
        async def go(db):
            await seed(db, idea("i1"), idea("i2", wallet=W2, x="x2", ticker="MOSS", fee="0x" + "f2" * 32))
            return (await store.count_ideas(db, D), await store.wallet_idea_today(db, W1, D),
                    await store.wallet_idea_today(db, W3, D), await store.fee_tx_used(db, "0x" + "F1" * 32),
                    await store.fee_tx_used(db, "0x" + "ee" * 32))
        self.assertEqual(run(go), (2, True, False, True, False))

    def test_fee_tx_can_only_pay_for_one_idea(self):
        async def go(db):
            await seed(db, idea("i1"))
            with self.assertRaises(DBAPIError):
                await store.insert_idea(db, idea("i2", wallet=W2, x="x2", ticker="MOSS"))   # same fee_tx
        run(go)

    def test_ideas_are_immutable_and_cannot_be_deleted(self):
        async def go(db):
            await seed(db, idea("i1"))
            for sql in ("UPDATE gf_ideas SET name = 'Hacked'", "UPDATE gf_ideas SET lore = 'x'",
                        "UPDATE gf_ideas SET image_sha256 = 'b'", "UPDATE gf_ideas SET wallet = '0xdead'",
                        "UPDATE gf_ideas SET fee_tx = '0x1'", "DELETE FROM gf_ideas"):
                with self.assertRaises(DBAPIError, msg=sql):
                    await db.execute(text(sql))
                await db.rollback()
            # moderation and scores stay writable
            await db.execute(text("UPDATE gf_ideas SET status = 'rejected', reject_reason = 'x'"))
            await db.commit()
        run(go)

    def test_embedding_and_flags_round_trip(self):
        async def go(db):
            await seed(db, idea("i1"))
            row = await store.get_idea(db, "i1")
            return row["lore_embedding"], row["auto_flags"]
        emb, flags = run(go)
        self.assertEqual([round(x, 1) for x in emb], [0.1, 0.2, 0.3])
        self.assertEqual(flags, {"needs_visual_check": True})

    def test_ideas_of_round_filters_by_status_and_joins_the_creator(self):
        async def go(db):
            await seed(db, idea("i1"), idea("i2", wallet=W2, x="x2", ticker="MOSS", fee="0x" + "f2" * 32, status="pending_review"),
                       idea("i3", wallet=W3, x="x3", ticker="FERN", fee="0x" + "f3" * 32, status="rejected"))
            approved = await store.ideas_of_round(db, D, ["approved"])
            both = await store.ideas_of_round(db, D, ["approved", "pending_review"])
            return [i["idea_id"] for i in approved], [i["idea_id"] for i in both], approved[0]["x_handle"], approved[0]["x_followers"]
        self.assertEqual(run(go), (["i1"], ["i1", "i2"], "spore", 5000))

    def test_ideas_of_wallet_newest_first(self):
        async def go(db):
            await seed(db, idea("i1", minute=0), idea("i2", ticker="B", fee="0x" + "f2" * 32, minute=5, round_date=D))
            return [i["idea_id"] for i in await store.ideas_of_wallet(db, W1.upper().replace("0X", "0x"))]
        self.assertEqual(run(go), ["i2", "i1"])

    def test_review_queue_and_decisions(self):
        async def go(db):
            await seed(db, idea("i1", status="pending_review"), idea("i2", wallet=W2, x="x2", ticker="MOSS",
                                                                 fee="0x" + "f2" * 32, status="pending_review"))
            queue = [i["idea_id"] for i in await store.pending_review(db)]
            ok1 = await store.review_idea(db, "i1", "approved", "ignored", "team:a")
            ok2 = await store.review_idea(db, "i2", "rejected", "uses a brand logo", "team:a")
            await db.commit()
            r1, r2 = await store.get_idea(db, "i1"), await store.get_idea(db, "i2")
            return queue, ok1, ok2, (r1["status"], r1["reject_reason"], r1["reviewed_by"]), (r2["status"], r2["reject_reason"])
        self.assertEqual(run(go), (["i1", "i2"], True, True, ("approved", None, "team:a"), ("rejected", "uses a brand logo")))

    def test_a_winner_can_no_longer_be_reviewed(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.set_round(db, D, winner_idea_id="i1", status="announced")
            return await store.review_idea(db, "i1", "rejected", "too late", "team:a")
        self.assertFalse(run(go))

    def test_same_day_others_skips_rejected_ones(self):
        async def go(db):
            await seed(db, idea("i1"), idea("i2", wallet=W2, x="x2", ticker="MOSS", fee="0x" + "f2" * 32, status="rejected"))
            return [o["idea_id"] for o in await store.same_day_others(db, D)]
        self.assertEqual(run(go), ["i1"])

    def test_existing_tickers_looks_at_robinhood_tokens_and_launched_ideas(self):
        async def go(db):
            await seed(db, idea("i1", ticker="SPORE"))
            await db.execute(text("INSERT INTO tokens (mint, symbol, chain) VALUES ('0x1', 'mosh', 'robinhood'), "
                                  "('0x2', 'MOSH', 'solana'), ('0x3', 'OLD', 'robinhood')"))
            await store.create_launch_slot(db, "i1", NOW, "r")
            await db.commit()
            return (await store.existing_tickers(db, "MOSH"), await store.existing_tickers(db, "spore"),
                    await store.existing_tickers(db, "FREE"))
        mosh, spore, free = run(go)
        self.assertEqual(mosh, ["mosh"])
        self.assertEqual(spore, ["SPORE"])
        self.assertEqual(free, [])

    def test_write_scores(self):
        from app.services.gf_scoring import ScoredIdea
        async def go(db):
            await seed(db, idea("i1"))
            s = ScoredIdea("i1", NOW, 61.25, 55.5, 100.0, 78.7, 12, "x1", W1, {"cluster_id": "c7"})
            await store.write_scores(db, [s])
            r = await store.get_idea(db, "i1")
            return [float(r[k]) for k in ("score_credibility", "score_golem", "score_vote", "score_final")], r["votes_counted"], r["radar_cluster_id"]
        self.assertEqual(run(go), ([61.25, 55.5, 100.0, 78.7], 12, "c7"))

    def test_recent_wins_for_the_seven_day_cap(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.set_round(db, D, winner_idea_id="i1", status="announced", announced_at=NOW)
            inside = await store.recent_wins(db, NOW - timedelta(days=7))
            outside = await store.recent_wins(db, NOW + timedelta(minutes=1))
            return [(w["x_user_id"], w["wallet"]) for w in inside], outside
        self.assertEqual(run(go), ([("x1", W1)], []))

    def test_a_winner_is_final(self):
        async def go(db):
            await seed(db, idea("i1"), idea("i2", wallet=W2, x="x2", ticker="MOSS", fee="0x" + "f2" * 32))
            await store.set_round(db, D, winner_idea_id="i1")
            await db.commit()
            with self.assertRaises(DBAPIError):
                await store.set_round(db, D, winner_idea_id="i2")
            await db.rollback()
            with self.assertRaises(DBAPIError):
                await db.execute(text("DELETE FROM gf_rounds"))
        run(go)


class TestVotes(PgCase):
    def test_one_vote_per_wallet_and_the_last_one_counts(self):
        async def go(db):
            await seed(db, idea("i1"), idea("i2", wallet=W2, x="x2", ticker="MOSS", fee="0x" + "f2" * 32))
            await store.upsert_vote(db, D, W3, "i1", "0xsig1", 1500.0, 100)
            await store.upsert_vote(db, D, W3, "i2", "0xsig2", 1600.0, 200)    # moved to another idea
            await db.commit()
            v = await store.get_vote(db, D, W3)
            return v["idea_id"], v["signature"], float(v["epc_balance_vote"]), v["signed_at"], await store.vote_counts(db, D)
        self.assertEqual(run(go), ("i2", "0xsig2", 1600.0, 200, {"i2": 1}))

    def test_vote_events_feed_the_hourly_limit(self):
        async def go(db):
            await seed(db, idea("i1"))
            for k in range(3):
                await store.upsert_vote(db, D, W3, "i1", f"0xs{k}", 1.0, k)
            await db.commit()
            return (await store.vote_changes_since(db, W3, datetime.now(timezone.utc) - timedelta(hours=1)),
                    await store.vote_changes_since(db, W3, datetime.now(timezone.utc) + timedelta(hours=1)),
                    await store.vote_changes_since(db, W2, datetime.now(timezone.utc) - timedelta(hours=1)))
        self.assertEqual(run(go), (3, 0, 0))

    def test_close_marks_counted_and_counts_only_counted_votes(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.upsert_vote(db, D, W2, "i1", "s", 2000.0, 1)
            await store.upsert_vote(db, D, W3, "i1", "s", 2000.0, 1)
            await store.set_vote_close(db, D, W2, 2000.0, True)
            await store.set_vote_close(db, D, W3, 10.0, False)       # sold before the close
            await db.commit()
            votes = await store.votes_of_round(db, D)
            return (await store.vote_counts(db, D), await store.vote_counts(db, D, counted_only=True),
                    [(v["voter_wallet"], v["counted"], float(v["epc_balance_close"])) for v in votes])
        all_votes, counted, rows = run(go)
        self.assertEqual((all_votes, counted), ({"i1": 2}, {"i1": 1}))
        self.assertEqual(sorted(rows), sorted([(W2, True, 2000.0), (W3, False, 10.0)]))


class TestLaunches(PgCase):
    def test_slot_registration_totals_and_the_ca_is_final(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.create_launch_slot(db, "i1", NOW + timedelta(hours=3), "highest survival hour")
            await store.create_launch_slot(db, "i1", NOW, "ignored: already scheduled")        # idempotent
            slots = await store.pending_launch_slots(db)
            ok = await store.set_launch_registered(db, "i1", "0x" + "ca" * 20, "0x" + "aa" * 32, NOW, "0x" + "5b" * 20)
            again = await store.set_launch_registered(db, "i1", "0x" + "cb" * 20, "0x" + "bb" * 32, NOW, None)
            await db.commit()
            launched = await store.launches_with_ca(db)
            with self.assertRaises(DBAPIError):
                await db.execute(text("UPDATE gf_launches SET ca = '0xother'"))
            await db.rollback()
            return len(slots), slots[0]["launch_hour_reason"], ok, again, launched[0]["ca"], launched[0]["x_handle"]
        self.assertEqual(run(go), (1, "highest survival hour", True, False, "0x" + "ca" * 20, "spore"))

    def test_verdict_sync_from_the_watcher_and_lock(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.create_launch_slot(db, "i1", NOW, "r")
            await store.set_launch_registered(db, "i1", "0x" + "ca" * 20, "0x" + "aa" * 32, NOW, None)
            await db.execute(text("INSERT INTO goforge_launches (id, ca, launch_tx, verdict, verdict_at, peak_mc_usd) "
                                  "VALUES ('i1', '0x" + "ca" * 20 + "', '0x" + "aa" * 32 + "', 'reached_30k', now(), 31000)"))
            await db.commit()
            pending = await store.unsynced_verdicts(db)
            await store.copy_verdict(db, "i1", pending[0]["verdict"], pending[0]["verdict_at"], float(pending[0]["peak_mc_usd"]))
            await db.commit()
            again = await store.unsynced_verdicts(db)
            with self.assertRaises(DBAPIError):
                await db.execute(text("UPDATE gf_launches SET verdict = 'stalled'"))
            return len(pending), pending[0]["verdict"], again
        n, v, again = run(go)
        self.assertEqual((n, v, again), (1, "reached_30k", []))

    def test_distributions_are_idempotent_and_roll_up_into_the_totals(self):
        async def go(db):
            await seed(db, idea("i1"))
            await store.create_launch_slot(db, "i1", NOW, "r")
            await store.set_launch_registered(db, "i1", "0x" + "ca" * 20, "0x" + "aa" * 32, NOW, "0x" + "5b" * 20)
            d = dict(tx_hash="0x" + "d1" * 32, log_index=0, idea_id="i1", block=100, at=NOW,
                     creator_wei=10 ** 18, burn_wei=10 ** 18, epc_burned_wei=5 * 10 ** 21)
            first = await store.insert_distribution(db, d)
            dup = await store.insert_distribution(db, d)
            await store.insert_distribution(db, {**d, "tx_hash": "0x" + "d2" * 32, "block": 200, "creator_wei": 5 * 10 ** 17,
                                                  "burn_wei": 5 * 10 ** 17, "epc_burned_wei": 10 ** 21})
            await store.refresh_launch_totals(db, "i1")
            await db.commit()
            launch = await store.get_launch(db, "i1")
            totals = await store.totals(db)
            return first, dup, float(launch["fees_to_creator"]), float(launch["epc_burned"]), totals
        first, dup, fees, burned, totals = run(go)
        self.assertEqual((first, dup, fees, burned), (True, False, 1.5, 6000.0))
        self.assertEqual((totals["launches"], totals["fees_paid_to_creators_eth"], totals["epc_burned_from_fees"]), (1, 1.5, 6000.0))


class TestAuthState(PgCase):
    def test_a_nonce_works_once(self):
        async def go(db):
            await store.store_nonce(db, "n1")
            await db.commit()
            return await store.consume_nonce(db, "n1"), await store.consume_nonce(db, "n1"), await store.consume_nonce(db, "unknown")
        self.assertEqual(run(go), (True, False, False))

    def test_an_old_nonce_is_refused(self):
        async def go(db):
            await store.store_nonce(db, "n1")
            await db.execute(text("UPDATE gf_nonces SET created_at = now() - interval '11 minutes'"))
            await db.commit()
            return await store.consume_nonce(db, "n1")
        self.assertFalse(run(go))

    def test_oauth_state_is_single_use_and_expires(self):
        async def go(db):
            await store.store_oauth_state(db, "s1", W1.upper().replace("0X", "0x"), "verifier")
            await store.store_oauth_state(db, "s2", W2, "v2")
            await db.execute(text("UPDATE gf_oauth_states SET created_at = now() - interval '11 minutes' WHERE state = 's2'"))
            await db.commit()
            return await store.pop_oauth_state(db, "s1"), await store.pop_oauth_state(db, "s1"), await store.pop_oauth_state(db, "s2")
        first, second, expired = run(go)
        self.assertEqual(first, {"wallet": W1, "code_verifier": "verifier"})
        self.assertIsNone(second)
        self.assertIsNone(expired)

    def test_purge_removes_day_old_rows(self):
        async def go(db):
            await store.store_nonce(db, "old")
            await store.store_nonce(db, "new")
            await db.execute(text("UPDATE gf_nonces SET created_at = now() - interval '2 days' WHERE nonce = 'old'"))
            await store.purge_stale(db)
            return [r[0] for r in (await db.execute(text("SELECT nonce FROM gf_nonces"))).all()]
        self.assertEqual(run(go), ["new"])


class TestPosts(PgCase):
    def test_a_post_is_claimed_once_per_kind_and_ref(self):
        async def go(db):
            a = await store.claim_post(db, "winner", "2026-10-06", "text")
            b = await store.claim_post(db, "winner", "2026-10-06", "text again")
            c = await store.claim_post(db, "live", "2026-10-06", "other kind")
            await store.finish_post(db, "winner", "2026-10-06", "posted", tweet_id="123")
            await store.finish_post(db, "live", "2026-10-06", "failed", error="boom")
            await db.commit()
            rows = (await db.execute(text("SELECT kind, status, tweet_id, error, attempts, posted_at IS NOT NULL FROM gf_posts ORDER BY kind"))).all()
            return a, b, c, [tuple(r) for r in rows]
        a, b, c, rows = run(go)
        self.assertEqual((a, b, c), (True, False, True))
        self.assertEqual(rows, [("live", "failed", None, "boom", 1, False), ("winner", "posted", "123", None, 1, True)])


class TestGoforgeSchemaTriggers(PgCase):
    """The first GoForge schema (launch tracking) had never run against a real database."""

    def test_launch_rows_cannot_be_deleted_and_a_locked_verdict_is_final(self):
        async def go(db):
            await db.execute(text("INSERT INTO goforge_launches (id, ca, launch_tx) VALUES ('a', '0x1', '0x2')"))
            await db.execute(text("UPDATE goforge_launches SET peak_mc_usd = 20000 WHERE id = 'a'"))
            await db.execute(text("UPDATE goforge_launches SET verdict = 'stalled', verdict_at = now() WHERE id = 'a'"))
            await db.commit()
            for sql in ("DELETE FROM goforge_launches", "UPDATE goforge_launches SET verdict = 'reached_30k'",
                        "UPDATE goforge_launches SET peak_mc_usd = 99999", "UPDATE goforge_launches SET ca = '0xother'",
                        "UPDATE goforge_launches SET launch_tx = '0xother'"):
                with self.assertRaises(DBAPIError, msg=sql):
                    await db.execute(text(sql))
                await db.rollback()
        run(go)

    def test_identity_columns_are_immutable_once_set(self):
        async def go(db):
            await db.execute(text("INSERT INTO goforge_launches (id, ca, launch_tx) VALUES ('a', '0x1', '0x2')"))
            await db.execute(text("UPDATE goforge_launches SET launched_at = now(), pool_active_at = now() WHERE id = 'a'"))
            await db.commit()
            for sql in ("UPDATE goforge_launches SET launched_at = now() - interval '1 day'",
                        "UPDATE goforge_launches SET pool_active_at = now() - interval '1 day'"):
                with self.assertRaises(DBAPIError, msg=sql):
                    await db.execute(text(sql))
                await db.rollback()
        run(go)

    def test_schema_can_be_applied_twice(self):
        from app.db.gf_schema import ensure_gf_schema
        from app.db.goforge_schema import ensure_goforge_schema
        async def go(db):
            await ensure_goforge_schema(db)
            await ensure_gf_schema(db)
        run(go)


if __name__ == "__main__":
    unittest.main()
