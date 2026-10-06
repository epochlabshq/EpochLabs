import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sqlalchemy import text

import gf_pg
from app.core import goforge_config
from app.core.config import settings
from app.services import gf_store, gf_validation
from app.services import gf_service as svc
from app.services import gf_worker as worker
from test_gf_service import FakeChain, X, acct, seed_pool, add_votes, D, T, LORE, LORE2

CA, SP, LTX = "0x" + "ca" * 20, "0x" + "5b" * 20, "0x" + "aa" * 32


@asynccontextmanager
async def always_locked(key):
    yield True


class WorkerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gf_pg.setup_schema()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        asyncio.run(gf_pg.truncate_all())
        worker.reset_state()
        goforge_config.clear_runtime_entries()
        self.addCleanup(goforge_config.clear_runtime_entries)
        self.addCleanup(worker.reset_state)
        self.chain = FakeChain()
        self.deps = svc.Deps(chain=self.chain, embed=lambda ts: ([gf_validation.hash_embedding(t) for t in ts], "test"),
                             image_dir=Path(self.tmp.name), blocklists=gf_validation.load_blocklists())
        self.events = []

        async def broadcast(msg):
            self.events.append(msg)
        for p in (mock.patch.object(worker, "exclusive", always_locked), mock.patch.object(worker, "AsyncSessionLocal", gf_pg.session),
                  mock.patch.object(worker.manager, "broadcast", broadcast)):
            p.start()
            self.addCleanup(p.stop)

    def cycle(self, now):
        return asyncio.run(worker.run_gf_cycle(self.deps, now))

    def db(self, coro_fn):
        async def go():
            async with gf_pg.session() as db:
                return await coro_fn(db)
        return asyncio.run(go())

    def names(self):
        return [next(iter(e)) for e in self.events]

    def seed(self, n=2, votes=2):
        async def go(db):
            await seed_pool(db, self.chain, n)
            if votes:
                await add_votes(db, self.chain, "idea-1", 100, votes)
        self.db(go)

    async def status(self, db, d=D):
        return (await gf_store.get_round(db, d))["status"]


class TestCycleCost(WorkerCase):
    def test_an_idle_cycle_does_no_database_work(self):
        self.assertTrue(self.cycle(T(9)))                       # first cycle bootstraps and sweeps
        with mock.patch.object(worker, "AsyncSessionLocal", side_effect=AssertionError("touched the DB")), \
             mock.patch.object(worker, "exclusive", side_effect=AssertionError("took the lock")):
            self.assertFalse(self.cycle(T(9, 1)))
            self.assertFalse(self.cycle(T(11, 58)))

    def test_a_phase_boundary_wakes_the_worker(self):
        self.cycle(T(9))
        self.assertTrue(self.cycle(T(12)))
        self.assertFalse(self.cycle(T(12, 1)))
        self.assertTrue(self.cycle(T(20)))

    def test_the_periodic_sweep_runs_even_without_a_boundary(self):
        self.cycle(T(9))
        worker._state["sweep"] = time.time() - worker.SWEEP_SECONDS - 1
        self.assertTrue(self.cycle(T(9, 5)))


class TestLifecycle(WorkerCase):
    def test_the_whole_day_in_order(self):
        self.seed()
        self.cycle(T(9))
        self.assertEqual(self.db(self.status), "submit")
        self.assertEqual(self.events, [])

        self.cycle(T(12))
        self.assertEqual(self.db(self.status), "vote")
        self.assertEqual(self.events[-1], {"gf_phase": {"round_date": "2026-10-06", "phase": "vote"}})

        self.cycle(T(20))
        self.assertEqual(self.db(self.status), "scoring")
        rnd = self.db(lambda db: gf_store.get_round(db, D))
        self.assertEqual((rnd["winner_idea_id"], rnd["n_votes"]), ("idea-1", 2))
        self.assertIsNone(rnd["announced_at"])
        self.assertNotIn("gf_winner", self.names())             # the winner is not announced at 20:00

        self.cycle(T(20, 5))
        rnd = self.db(lambda db: gf_store.get_round(db, D))
        self.assertEqual((rnd["status"], rnd["announced_at"]), ("announced", T(20, 5)))
        winner = next(e["gf_winner"] for e in self.events if "gf_winner" in e)
        self.assertEqual((winner["idea_id"], winner["name"], winner["ticker"]), ("idea-1", "Idea 1 Keeper", "TK1"))
        slot = self.db(lambda db: gf_store.get_launch(db, "idea-1"))
        self.assertIsNotNone(slot["launch_due_at"])
        posts = self.db(lambda db: db.execute(text("SELECT kind, ref, status FROM gf_posts")))
        rows = asyncio.run(self._posts())
        self.assertEqual(rows, [("winner", "2026-10-06", "dry_run")])      # dry run: X posting is not enabled

    async def _posts(self):
        async with gf_pg.session() as db:
            return [tuple(r) for r in (await db.execute(text("SELECT kind, ref, status FROM gf_posts ORDER BY kind"))).all()]

    def test_repeated_cycles_never_announce_or_post_twice(self):
        self.seed()
        for t in (T(9), T(12), T(20), T(20, 5), T(20, 6), T(21), T(23, 59)):
            self.cycle(t)
            worker._state["sweep"] = 0              # force the sweep every time as well
        self.assertEqual(self.names().count("gf_winner"), 1)
        self.assertEqual(len(asyncio.run(self._posts())), 1)

    def test_a_round_with_no_votes_ends_in_no_launch_and_a_post_with_the_reason(self):
        self.seed(votes=0)
        for t in (T(9), T(12), T(20), T(20, 5)):
            self.cycle(t)
        rnd = self.db(lambda db: gf_store.get_round(db, D))
        self.assertEqual((rnd["status"], rnd["no_launch_reason"], rnd["winner_idea_id"]), ("no_launch", "no_votes", None))
        self.assertNotIn("gf_winner", self.names())
        body = self.db(lambda db: db.execute(text("SELECT text FROM gf_posts")))
        self.assertEqual(asyncio.run(self._posts()), [("no_launch", "2026-10-06", "dry_run")])

    def test_a_round_the_worker_slept_through_is_finished_when_it_comes_back(self):
        async def stuck(db):
            old = date(2026, 10, 4)
            await gf_store.ensure_round(db, old)
            await gf_store.upsert_creator(db, X("x1", "c1"), acct(1).address)
            await gf_store.insert_idea(db, dict(idea_id="old-1", round_date=old, x_user_id="x1", wallet=acct(1).address.lower(), name="Old Keeper",
                                                ticker="OLD", lore=LORE, image_url="/x", image_sha256="a" * 64, fee_tx="0x" + "0a" * 32,
                                                status="approved", reject_reason=None, auto_flags={}, lore_embedding=None, submitted_at=T(8, day=4)))
            await gf_store.set_round(db, old, status="vote")
            a = acct(60)
            await gf_store.upsert_vote(db, old, a.address, "old-1", "sig", 5000.0, 1)
            self.chain.balances[a.address.lower()] = 5000.0
            await db.commit()
        self.db(stuck)
        self.cycle(T(9))                                        # worker back on 6 Oct, two days late
        rnd = self.db(lambda db: gf_store.get_round(db, date(2026, 10, 4)))
        self.assertEqual((rnd["status"], rnd["winner_idea_id"]), ("announced", "old-1"))
        self.assertEqual(self.db(self.status), "submit")        # and today's round is untouched

    def test_a_round_left_in_scoring_is_announced_after_a_restart(self):
        self.seed()
        self.cycle(T(9))
        self.cycle(T(20, 2))                                    # scored, then the process dies before 20:05
        worker.reset_state()
        self.cycle(T(1, day=7))                                 # back the next morning
        self.assertEqual(self.db(self.status), "announced")
        self.assertEqual(self.names().count("gf_winner"), 1)


class TestSweep(WorkerCase):
    def register(self):
        async def go(db):
            await seed_pool(db, self.chain, 2)
            await add_votes(db, self.chain, "idea-1", 100, 2)
            await svc.score_round(db, self.deps, D, T(20))
            await svc.announce_round(db, self.deps, D, T(20, 5))
            self.chain.codes = {CA: True, SP: True}
            await svc.register_launch(db, self.deps, idea_id="idea-1", ca=CA, launch_tx=LTX, splitter=SP, registered_by="t", now=T(21))
        self.db(go)
        goforge_config.clear_runtime_entries()

    def test_bootstrap_restores_the_watcher_entry_and_the_trading_exclusion(self):
        self.register()
        self.assertNotIn(CA, goforge_config.goforge_cas())          # "the process restarted"
        self.cycle(T(22))
        self.assertIn(CA, goforge_config.goforge_cas())
        self.assertIn("idea-1", [e.id for e in goforge_config.all_entries()])

    def test_the_live_post_is_queued_once_with_the_ca_and_no_dollar_sign(self):
        self.register()
        with mock.patch.object(settings, "GF_PONS_URL_TEMPLATE", "https://pons.test/token/{ca}"):
            self.cycle(T(22))
            worker._state["sweep"] = 0
            self.cycle(T(22, 5))
        rows = asyncio.run(self._posts())
        self.assertEqual([r for r in rows if r[0] == "live"], [("live", "idea-1", "dry_run")])
        body = asyncio.run(self._text("live"))
        self.assertIn(CA, body)
        self.assertIn("https://pons.test/token/" + CA, body)
        self.assertNotRegex(body, r"\$[A-Za-z]")

    async def _text(self, kind):
        async with gf_pg.session() as db:
            return (await db.execute(text("SELECT text FROM gf_posts WHERE kind = :k"), {"k": kind})).scalar()

    async def _posts(self):
        async with gf_pg.session() as db:
            return [tuple(r) for r in (await db.execute(text("SELECT kind, ref, status FROM gf_posts ORDER BY kind"))).all()]

    def test_the_verdict_is_copied_posted_once_and_broadcast(self):
        self.register()
        self.cycle(T(22))
        async def lock(db):
            await db.execute(text("UPDATE goforge_launches SET verdict = 'reached_30k', verdict_at = now(), peak_mc_usd = 41000 WHERE id = 'idea-1'"))
            await db.commit()
        self.db(lock)
        worker._state["sweep"] = 0
        self.cycle(T(22, 30))
        worker._state["sweep"] = 0
        self.cycle(T(22, 45))
        self.assertEqual([e for e in self.events if "gf_verdict" in e], [{"gf_verdict": {"idea_id": "idea-1", "verdict": "reached_30k"}}])
        body = asyncio.run(self._text("verdict"))
        self.assertIn("reached the 30K market cap", body)
        launch = self.db(lambda db: gf_store.get_launch(db, "idea-1"))
        self.assertEqual(launch["verdict"], "reached_30k")
        creator = self.db(lambda db: gf_store.creator_by_x(db, "x1"))
        self.assertEqual(creator["wins_reached_30k"], 1)

    def test_distributions_are_indexed_during_the_sweep(self):
        self.register()
        self.chain.head = 1100
        self.chain.dist[SP] = [dict(tx_hash="0x" + "d1" * 32, log_index=0, block=1020, at=T(22), creator_wei=10 ** 18, burn_wei=10 ** 18,
                                    epc_burned_wei=4000 * 10 ** 18)]
        self.cycle(T(22))
        launch = self.db(lambda db: gf_store.get_launch(db, "idea-1"))
        self.assertEqual((float(launch["fees_to_creator"]), float(launch["epc_burned"])), (1.0, 4000.0))

    def test_a_locked_worker_does_nothing(self):
        @asynccontextmanager
        async def busy(key):
            yield False
        self.seed()
        with mock.patch.object(worker, "exclusive", busy):
            self.assertFalse(self.cycle(T(12)))
        self.assertEqual(self.db(self.status), "submit")         # nothing was advanced


if __name__ == "__main__":
    unittest.main()
