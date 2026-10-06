import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eth_account import Account
from eth_account.messages import encode_typed_data
from PIL import Image
from sqlalchemy import text

import gf_pg
from app.core import goforge_config
from app.core.config import settings
from app.services import gf_chain, gf_identity, gf_rounds, gf_scoring, gf_store, gf_validation
from app.services import gf_service as svc
from app.services.chain_reader import TOPIC_TRANSFER, address_topic

D = date(2026, 10, 6)
CHAIN = settings.CHAIN_ID
EPC = settings.EPOCH_TOKEN_CA
FEE_WEI = int(settings.GF_SUBMIT_FEE_EPC) * 10 ** 18


def T(hour, minute=0, day=6):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


SUBMIT_AT, VOTE_AT, CLOSE_AT, ANNOUNCE_AT = T(9), T(15), T(20), T(20, 5)
LORE = ("A tiny mushroom who remembers every holder by name and refuses to forget a single one of them, even "
        "when the market forgets the mushroom itself.")
LORE2 = ("The lantern frog sings the tide back every evening so that the fishing boats can find the harbour lights "
         "again after the long grey storms of the autumn season.")


def key(n):
    return "0x" + f"{n:064x}"


def acct(n):
    return Account.from_key(key(n))


def png(size=512):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (210, 140, 40)).save(buf, "PNG")
    return buf.getvalue()


def X(i, handle, **kw):
    base = dict(x_user_id=i, x_handle=handle, x_verified=True, x_created_at=T(0) - timedelta(days=800), x_followers=5000)
    base.update(kw)
    return base


class FakeChain:
    def __init__(self):
        self.balances, self.first, self.fee_txs, self.codes = {}, {}, {}, {}
        self.head = 1000
        self.dist: dict[str, list] = {}
        self.eth = {}
        self.sent = []
        self.calls = []

    async def epc_balance(self, w):
        self.calls.append(("balance", w.lower()))
        return self.balances.get(w.lower())

    async def first_activity(self, w):
        return self.first.get(w.lower())

    async def fee_tx(self, h):
        return self.fee_txs.get(h.lower())

    async def has_code(self, a):
        return self.codes.get(a.lower())

    async def latest_block(self):
        return self.head

    async def distributions(self, splitter, frm, to):
        return [d for d in self.dist.get(splitter.lower(), []) if frm <= d["block"] <= to]

    async def eth_balance(self, a):
        return self.eth.get(a.lower())

    async def send_distribute(self, splitter, min_out=0):
        self.sent.append(splitter)
        return "0x" + "77" * 32

    def add_fee_tx(self, tx_hash, sender, amount_wei=FEE_WEI, at=None, token=EPC, to=gf_chain.DEAD_ADDRESS, status="0x1"):
        receipt = {"status": status, "blockNumber": "0x10", "logs": [{
            "address": token, "topics": [TOPIC_TRANSFER, address_topic(sender), address_topic(to)], "data": hex(amount_wei)}]}
        self.fee_txs[tx_hash.lower()] = ({"from": sender}, receipt, at or SUBMIT_AT - timedelta(hours=1))


def deps(chain, tmp, **kw):
    d = dict(chain=chain, embed=lambda ts: ([gf_validation.hash_embedding(t) for t in ts], "test embedder"),
             image_dir=Path(tmp), blocklists=gf_validation.load_blocklists())
    d.update(kw)
    return svc.Deps(**d)


def run(coro_fn):
    async def go():
        await gf_pg.truncate_all()
        async with gf_pg.session() as db:
            return await coro_fn(db)
    return asyncio.run(go())


class PgCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gf_pg.setup_schema()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        goforge_config.clear_runtime_entries()
        self.addCleanup(goforge_config.clear_runtime_entries)

    async def creator(self, db, n, handle=None, **kw):
        a = acct(n)
        await gf_store.upsert_creator(db, X(f"x{n}", handle or f"user{n}", **kw), a.address)
        await db.commit()
        return a

    def submit(self, db, chain, a, *, n=1, name="Spore Keeper", ticker="SPORE", lore=LORE, image=None, tx=None, now=SUBMIT_AT,
               add_fee=True, **kw):
        tx = tx or "0x" + f"{n:02x}" * 32
        if add_fee and tx.lower() not in chain.fee_txs:
            chain.add_fee_tx(tx, a.address, at=now - timedelta(hours=1))
        return svc.submit_idea(db, deps(chain, self.tmp.name), wallet=a.address, name=name, ticker=ticker, lore=lore,
                               image=image or png(), fee_tx=tx, now=now)


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

class TestSubmit(PgCase):
    def err(self, coro_fn, status=None, code=None):
        async def go(db):
            with self.assertRaises(svc.ServiceError) as ctx:
                await coro_fn(db)
            return ctx.exception
        e = run(go)
        if status:
            self.assertEqual(e.status, status)
        if code:
            self.assertEqual(e.code, code)
        return e

    def test_happy_path_goes_to_manual_review_and_stores_the_image_by_hash(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1, "sporefan")
            out = await self.submit(db, chain, a)
            row = await gf_store.get_idea(db, out["idea_id"])
            return out, row
        out, row = run(go)
        self.assertEqual((out["status"], out["reject_reason"], out["ticker"], out["creator_handle"]), ("pending_review", None, "SPORE", "sporefan"))
        self.assertEqual(row["wallet"], acct(1).address.lower())
        self.assertEqual(row["round_date"], D)
        self.assertEqual(row["auto_flags"]["needs_visual_check"], True)
        self.assertEqual(row["auto_flags"]["embedder"], "test embedder")
        self.assertEqual(row["auto_flags"]["fee_epc_burned"], 1000.0)
        saved = Path(self.tmp.name) / f"{row['image_sha256']}.png"
        self.assertTrue(saved.exists())
        self.assertEqual(row["image_url"], f"/api/goforge/images/{row['image_sha256']}.png")
        self.assertEqual(saved.read_bytes(), png())

    def test_public_and_own_views_hide_the_private_fields(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            out = await self.submit(db, chain, a)
            return out, svc.public_idea(await gf_store.get_idea(db, out["idea_id"]), votes=3)
        out, pub = run(go)
        for blob in (out, pub):
            for secret in ("wallet", "fee_tx", "auto_flags", "lore_embedding", "x_user_id"):
                self.assertNotIn(secret, blob)
        self.assertEqual(pub["votes"], 3)

    def test_closed_outside_the_submit_window(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            await self.submit(db, chain, a, now=T(12))
        self.err(go, 409, "submissions_closed")
        self.err(lambda db: self._at(db, chain, T(21)), 409, "submissions_closed")

    async def _at(self, db, chain, now):
        a = await self.creator(db, 1)
        await self.submit(db, chain, a, now=now)

    def test_open_from_the_first_second_and_until_the_last(self):
        for now in (T(0), T(11, 59)):
            chain = FakeChain()

            async def go(db):
                a = await self.creator(db, 1)
                return (await self.submit(db, chain, a, now=now))["status"]
            self.assertEqual(run(go), "pending_review", now)

    def test_x_account_must_be_linked(self):
        chain = FakeChain()

        async def go(db):
            a = acct(1)
            await self.submit(db, chain, a)
        self.err(go, 403, "x_not_linked")

    def test_team_cannot_submit(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 5)
            with mock.patch.object(settings, "GF_TEAM_WALLETS", a.address):
                await self.submit(db, chain, a, n=5)
        self.err(go, 403, "team_not_allowed")

        async def go2(db):
            a = await self.creator(db, 6)
            with mock.patch.object(settings, "GF_TEAM_X_USER_IDS", "x6"):
                await self.submit(db, chain, a, n=6)
        self.err(go2, 403, "team_not_allowed")

    def test_invalid_fields_and_image_are_reported_together(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            await self.submit(db, chain, a, name="A", ticker="$$", lore="short", image=b"junk")
        e = self.err(go, 422, "invalid_idea")
        self.assertEqual({p["field"] for p in e.extra["problems"]}, {"name", "ticker", "lore", "image"})

    def test_a_non_square_image_is_refused(self):
        chain = FakeChain()
        buf = io.BytesIO()
        Image.new("RGB", (700, 600)).save(buf, "PNG")

        async def go(db):
            a = await self.creator(db, 1)
            await self.submit(db, chain, a, image=buf.getvalue())
        e = self.err(go, 422, "invalid_idea")
        self.assertEqual(e.extra["problems"][0]["code"], "ratio")

    def test_fee_rules(self):
        cases = {
            "unreadable": (lambda c, a, h: None, 503, "chain_unavailable"),   # the fake knows no such tx
            "too_low": (lambda c, a, h: c.add_fee_tx(h, a.address, amount_wei=FEE_WEI - 1), 422, "fee_tx_rejected"),
            "other_sender": (lambda c, a, h: c.add_fee_tx(h, acct(99).address), 422, "fee_tx_rejected"),
            "not_to_burn": (lambda c, a, h: c.add_fee_tx(h, a.address, to="0x" + "12" * 20), 422, "fee_tx_rejected"),
            "other_token": (lambda c, a, h: c.add_fee_tx(h, a.address, token="0x" + "99" * 20), 422, "fee_tx_rejected"),
            "reverted": (lambda c, a, h: c.add_fee_tx(h, a.address, status="0x0"), 422, "fee_tx_rejected"),
            "too_old": (lambda c, a, h: c.add_fee_tx(h, a.address, at=SUBMIT_AT - timedelta(hours=49)), 422, "fee_tx_rejected"),
        }
        for name, (setup, status, code) in cases.items():
            chain = FakeChain()
            h = "0x" + "ab" * 32

            async def go(db):
                a = await self.creator(db, 1)
                setup(chain, a, h)
                await self.submit(db, chain, a, tx=h, add_fee=False)
            with self.subTest(name):
                self.err(go, status, code)

    def test_exact_fee_amount_passes_and_a_malformed_hash_is_refused(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            chain.add_fee_tx("0x" + "cd" * 32, a.address, amount_wei=FEE_WEI)
            return (await self.submit(db, chain, a, tx="0x" + "cd" * 32))["status"]
        self.assertEqual(run(go), "pending_review")

        async def bad(db):
            a = await self.creator(db, 2)
            await svc.submit_idea(db, deps(chain, self.tmp.name), wallet=a.address, name="Spore", ticker="SPORE", lore=LORE,
                                  image=png(), fee_tx="0x123", now=SUBMIT_AT)
        self.err(bad, 422, "fee_tx_invalid")

    def test_a_fee_tx_cannot_pay_for_two_ideas(self):
        chain = FakeChain()

        async def go(db):
            a, b = await self.creator(db, 1), await self.creator(db, 2)
            await self.submit(db, chain, a, tx="0x" + "ee" * 32)
            chain.add_fee_tx("0x" + "ee" * 32, b.address)       # even if it matched wallet b, the hash is spent
            await self.submit(db, chain, b, tx="0x" + "ee" * 32, ticker="MOSS", name="Moss Keeper", lore=LORE2)
        self.err(go, 409, "fee_tx_used")

    def test_one_idea_per_wallet_per_day_but_a_new_day_is_fine(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            await self.submit(db, chain, a, n=1)
            try:
                await self.submit(db, chain, a, n=2, ticker="MOSS", name="Moss Keeper", lore=LORE2)
            except svc.ServiceError as e:
                first = e.code
            nxt = await self.submit(db, chain, a, n=3, ticker="MOSS", name="Moss Keeper", lore=LORE2, now=T(9, day=7))
            return first, nxt["round_date"]
        self.assertEqual(run(go), ("already_submitted_today", "2026-10-07"))

    def test_slots_stop_at_the_cap(self):
        chain = FakeChain()

        async def go(db):
            with mock.patch.object(settings, "GF_MAX_IDEAS_PER_DAY", 2):
                for n in (1, 2):
                    a = await self.creator(db, n)
                    await self.submit(db, chain, a, n=n, ticker=f"TK{n}", name=f"Name {n} Keeper", lore=(LORE if n == 1 else LORE2))
                a = await self.creator(db, 3)
                await self.submit(db, chain, a, n=3, ticker="TK3", name="Name 3 Keeper",
                                  lore="The glass whale carries every forgotten lighthouse across the sea at midnight, singing quietly.")
        self.err(go, 409, "slots_full")

    def test_concurrent_submissions_cannot_overfill_the_slots(self):
        chain = FakeChain()
        lores = [f"Story number {i}: " + " ".join(f"word{i}x{j}" for j in range(14)) + " ends here." for i in range(5)]

        async def go(db):
            pass

        async def main():
            await gf_pg.truncate_all()
            async with gf_pg.session() as db0:
                accounts = [await self.creator(db0, n) for n in range(1, 6)]

            async def one(i, a):
                async with gf_pg.session() as db:
                    try:
                        await self.submit(db, chain, a, n=i + 1, ticker=f"CC{i}", name=f"Concurrent {i} Keeper", lore=lores[i])
                        return "ok"
                    except svc.ServiceError as e:
                        return e.code
            with mock.patch.object(settings, "GF_MAX_IDEAS_PER_DAY", 2):
                return await asyncio.gather(*(one(i, a) for i, a in enumerate(accounts)))
        results = asyncio.run(main())
        self.assertEqual(sorted(results), ["ok", "ok", "slots_full", "slots_full", "slots_full"])

    def test_automatic_rejections_keep_the_fee_and_show_the_reason(self):
        cases = [
            (dict(name="NVIDIA Coin", ticker="NVC"), "brand"),
            (dict(name="Spore Keeper", ticker="NVDA"), "stock"),
            (dict(name="Spore Keeper", ticker="MOSH"), "already used"),
        ]
        for kw, text_ in cases:
            chain = FakeChain()

            async def go(db):
                await db.execute(text("INSERT INTO tokens (mint, symbol, chain) VALUES ('0x1', 'MOSH', 'robinhood')"))
                a = await self.creator(db, 1)
                out = await self.submit(db, chain, a, **kw)
                spent = await gf_store.fee_tx_used(db, "0x" + "01" * 32)
                return out, spent
            out, spent = run(go)
            with self.subTest(kw):
                self.assertEqual(out["status"], "rejected")
                self.assertIn(text_, out["reject_reason"].lower())
                self.assertTrue(spent)       # the fee is not refunded: the tx stays used

    def test_a_near_copy_of_todays_idea_is_rejected(self):
        chain = FakeChain()

        async def go(db):
            a, b = await self.creator(db, 1), await self.creator(db, 2)
            first = await self.submit(db, chain, a, n=1)
            second = await self.submit(db, chain, b, n=2, name="Totally Different", ticker="DIFF", lore=LORE + " Truly.")
            third = await self.submit(db, chain, await self.creator(db, 3), n=3, name="Another One", ticker="SPORE", lore=LORE2)
            return first["status"], second, third
        first, second, third = run(go)
        self.assertEqual(first, "pending_review")
        self.assertEqual((second["status"], third["status"]), ("rejected", "rejected"))
        self.assertIn("almost the same lore", second["reject_reason"])
        self.assertIn("same ticker" if False else "ticker", third["reject_reason"].lower())

    def test_image_classifier_hook_can_reject(self):
        chain = FakeChain()

        async def go(db):
            a = await self.creator(db, 1)
            chain.add_fee_tx("0x" + "01" * 32, a.address)
            return await svc.submit_idea(db, deps(chain, self.tmp.name, image_moderator=lambda b: ["nsfw"]), wallet=a.address,
                                         name="Spore Keeper", ticker="SPORE", lore=LORE, image=png(), fee_tx="0x" + "01" * 32, now=SUBMIT_AT)
        out = run(go)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("image", out["reject_reason"].lower())


# ---------------------------------------------------------------------------
# Vote
# ---------------------------------------------------------------------------

def sign_vote(account, idea_id, signed_at, round_date="2026-10-06", voter=None, chain_id=CHAIN):
    typed = gf_identity.vote_typed_data(round_date=round_date, idea_id=idea_id, voter=voter or account.address,
                                        signed_at=signed_at, chain_id=chain_id)
    return Account.sign_message(encode_typed_data(full_message=typed), account.key).signature.hex()


async def seed_pool(db, chain, n_ideas=2):
    """n approved ideas by creators 1..n, voters 11.. all eligible. Returns (ideas, creators)."""
    await gf_store.ensure_round(db, D)
    ideas, creators = [], []
    for n in range(1, n_ideas + 1):
        a = acct(n)
        await gf_store.upsert_creator(db, X(f"x{n}", f"creator{n}"), a.address)
        row = dict(idea_id=f"idea-{n}", round_date=D, x_user_id=f"x{n}", wallet=a.address.lower(), name=f"Idea {n} Keeper",
                   ticker=f"TK{n}", lore=LORE if n % 2 else LORE2, image_url=f"/thumbnails/goforge/{n}.png", image_sha256=f"{n}" * 64,
                   fee_tx="0x" + f"{n:02x}" * 32, status="approved", reject_reason=None, auto_flags={},
                   lore_embedding=gf_validation.hash_embedding(LORE if n % 2 else LORE2), submitted_at=T(8) + timedelta(minutes=n))
        await gf_store.insert_idea(db, row)
        ideas.append(row)
        creators.append(a)
    await db.commit()
    return ideas, creators


def voter(chain, n, balance=5000.0, age_days=30, now=VOTE_AT):
    a = acct(n)
    chain.balances[a.address.lower()] = balance
    chain.first[a.address.lower()] = now - timedelta(days=age_days)
    return a


class TestVote(PgCase):
    def vote(self, db, chain, a, idea_id, signed_at=None, now=VOTE_AT, **kw):
        signed_at = signed_at or int(now.timestamp())
        sig = kw.pop("signature", None) or sign_vote(a, idea_id, signed_at)
        return svc.cast_vote(db, deps(chain, self.tmp.name), wallet=a.address, idea_id=idea_id, round_date=kw.pop("round_date", "2026-10-06"),
                             signed_at=signed_at, signature=sig, now=now)

    def err(self, coro_fn, status, code):
        async def go(db):
            with self.assertRaises(svc.ServiceError) as ctx:
                await coro_fn(db)
            return ctx.exception
        e = run(go)
        self.assertEqual((e.status, e.code), (status, code))
        return e

    def test_vote_is_stored_with_the_balance_and_counted(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            out = await self.vote(db, chain, v, "idea-1")
            row = await gf_store.get_vote(db, D, v.address)
            return out, float(row["epc_balance_vote"]), row["signature"] != ""
        out, bal, has_sig = run(go)
        self.assertEqual((out["changed"], out["votes"], bal, has_sig), (True, {"idea-1": 1}, 5000.0, True))

    def test_changing_the_vote_moves_it_and_the_last_one_counts(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            await self.vote(db, chain, v, "idea-1", signed_at=int(VOTE_AT.timestamp()))
            out = await self.vote(db, chain, v, "idea-2", signed_at=int(VOTE_AT.timestamp()) + 5)
            return out["votes"]
        self.assertEqual(run(go), {"idea-2": 1})

    def test_voting_the_same_idea_again_is_a_noop(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            await self.vote(db, chain, v, "idea-1", signed_at=int(VOTE_AT.timestamp()))
            out = await self.vote(db, chain, v, "idea-1", signed_at=int(VOTE_AT.timestamp()) + 5)
            events = (await db.execute(text("SELECT count(*) FROM gf_vote_events"))).scalar()
            return out["changed"], events
        self.assertEqual(run(go), (False, 1))

    def test_an_older_signature_cannot_undo_a_newer_vote(self):
        chain = FakeChain()
        t = int(VOTE_AT.timestamp())

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            await self.vote(db, chain, v, "idea-1", signed_at=t)
            await self.vote(db, chain, v, "idea-2", signed_at=t + 20)
            await self.vote(db, chain, v, "idea-1", signed_at=t + 10)      # replayed older signature
        self.err(go, 409, "stale_signature")

    def test_bad_wrong_and_foreign_signatures(self):
        chain = FakeChain()

        async def wrong_idea(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            await self.vote(db, chain, v, "idea-1", signature=sign_vote(v, "idea-2", int(VOTE_AT.timestamp())))
        self.err(wrong_idea, 401, "bad_signature")

        async def other_wallet(db):
            await seed_pool(db, chain)
            v, other = voter(chain, 11), voter(chain, 12)
            await self.vote(db, chain, v, "idea-1", signature=sign_vote(other, "idea-1", int(VOTE_AT.timestamp()), voter=v.address))
        self.err(other_wallet, 401, "bad_signature")

        async def stale_time(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            await self.vote(db, chain, v, "idea-1", signed_at=int(VOTE_AT.timestamp()) - 7200)
        self.err(stale_time, 401, "bad_signature")

    def test_phase_and_round_checks(self):
        chain = FakeChain()

        async def early(db):
            await seed_pool(db, chain)
            await self.vote(db, chain, voter(chain, 11), "idea-1", now=T(11, 59))
        self.err(early, 409, "voting_closed")

        async def late(db):
            await seed_pool(db, chain)
            await self.vote(db, chain, voter(chain, 11), "idea-1", now=T(20))
        self.err(late, 409, "voting_closed")

        async def wrong_round(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            t = int(VOTE_AT.timestamp())
            await svc.cast_vote(db, deps(chain, self.tmp.name), wallet=v.address, idea_id="idea-1", round_date="2026-10-05",
                                signed_at=t, signature=sign_vote(v, "idea-1", t, round_date="2026-10-05"), now=VOTE_AT)
        self.err(wrong_round, 409, "wrong_round")

    def test_only_approved_ideas_of_todays_round_can_be_voted(self):
        chain = FakeChain()

        async def go(db):
            ideas, _ = await seed_pool(db, chain)
            await gf_store.review_idea(db, "idea-2", "rejected", "no", "t")
            await db.commit()
            await self.vote(db, chain, voter(chain, 11), "idea-2")
        self.err(go, 404, "idea_not_votable")
        self.err(lambda db: self._unknown(db, chain), 404, "idea_not_votable")

    async def _unknown(self, db, chain):
        await seed_pool(db, chain)
        await self.vote(db, chain, voter(chain, 11), "does-not-exist")

    def test_eligibility_balance_age_and_fail_closed(self):
        cases = [
            ("balance_too_low", dict(balance=999.0), 403),
            ("wallet_too_new", dict(age_days=6), 403),
        ]
        for reason, kw, status in cases:
            chain = FakeChain()

            async def go(db):
                await seed_pool(db, chain)
                await self.vote(db, chain, voter(chain, 11, **kw), "idea-1")
            with self.subTest(reason):
                e = self.err(go, status, "not_eligible")
                self.assertEqual(e.extra["reason"], reason)

        chain = FakeChain()

        async def no_balance(db):
            await seed_pool(db, chain)
            v = acct(11)
            chain.first[v.address.lower()] = VOTE_AT - timedelta(days=30)       # balance unreadable -> None
            await self.vote(db, chain, v, "idea-1")
        e = self.err(no_balance, 503, "not_eligible")
        self.assertEqual(e.extra["reason"], "balance_unavailable")

        async def no_age(db):
            await seed_pool(db, chain)
            v = acct(12)
            chain.balances[v.address.lower()] = 5000.0                           # first activity unknown -> None
            await self.vote(db, chain, v, "idea-1")
        e = self.err(no_age, 503, "not_eligible")
        self.assertEqual(e.extra["reason"], "wallet_age_unknown")

    def test_exact_thresholds_pass(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain)
            return (await self.vote(db, chain, voter(chain, 11, balance=1000.0, age_days=7), "idea-1"))["changed"]
        self.assertTrue(run(go))

    def test_a_creator_cannot_vote_for_their_own_idea(self):
        chain = FakeChain()

        async def by_wallet(db):
            _, creators = await seed_pool(db, chain)
            c = creators[0]
            chain.balances[c.address.lower()], chain.first[c.address.lower()] = 5000.0, VOTE_AT - timedelta(days=30)
            await self.vote(db, chain, c, "idea-1")
        self.err(by_wallet, 403, "self_vote")

        async def by_x_account(db):
            # after a manual wallet change the same X account holds a new wallet: still the same creator
            _, creators = await seed_pool(db, chain)
            new = voter(chain, 50)
            await gf_store.change_wallet(db, "x1", new.address, "key rotated", "team")
            await db.commit()
            await self.vote(db, chain, new, "idea-1")
        self.err(by_x_account, 403, "self_vote")

    def test_the_team_cannot_vote(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            with mock.patch.object(settings, "GF_TEAM_WALLETS", v.address):
                await self.vote(db, chain, v, "idea-1")
        self.err(go, 403, "team_not_allowed")

    def test_vote_changes_are_rate_limited_per_hour(self):
        chain = FakeChain()
        t = int(VOTE_AT.timestamp())

        async def go(db):
            await seed_pool(db, chain)
            v = voter(chain, 11)
            with mock.patch.object(settings, "GF_VOTE_CHANGES_PER_HOUR", 2):
                await self.vote(db, chain, v, "idea-1", signed_at=t)
                await self.vote(db, chain, v, "idea-2", signed_at=t + 1)
                await self.vote(db, chain, v, "idea-1", signed_at=t + 2)
        self.err(go, 429, "vote_rate_limited")


# ---------------------------------------------------------------------------
# Scoring and the announcement
# ---------------------------------------------------------------------------

async def add_votes(db, chain, idea_id, wallets_start, n, balance=5000.0):
    for k in range(n):
        a = acct(wallets_start + k)
        chain.balances[a.address.lower()] = balance
        await gf_store.upsert_vote(db, D, a.address, idea_id, "0xsig", balance, 1)
    await db.commit()


class TestRound(PgCase):
    def score(self, db, chain, now=CLOSE_AT, **kw):
        return svc.score_round(db, deps(chain, self.tmp.name, **kw), D, now)

    def test_a_full_round_scores_picks_the_winner_and_publishes_a_reproducible_table(self):
        chain = FakeChain()

        async def go(db):
            ideas, _ = await seed_pool(db, chain, 3)
            await db.execute(text("UPDATE gf_creators SET x_followers = 100000, x_verified = true WHERE x_user_id = 'x2'"))
            await add_votes(db, chain, "idea-1", 100, 3)
            await add_votes(db, chain, "idea-2", 200, 2)
            await add_votes(db, chain, "idea-3", 300, 1)
            res = await self.score(db, chain)
            rnd = await gf_store.get_round(db, D)
            rows = await gf_store.ideas_of_round(db, D, ["approved"])
            return res, rnd, rows
        res, rnd, rows = run(go)
        self.assertEqual(res["n_votes"], 6)
        self.assertEqual(rnd["status"], "scoring")
        self.assertEqual(rnd["n_votes"], 6)
        self.assertEqual(rnd["n_ideas"], 3)
        self.assertEqual(rnd["scoreboard_sha256"], res["scoreboard_sha256"])
        self.assertEqual(rnd["winner_idea_id"], res["winner"].idea_id)
        for r in rows:
            self.assertEqual(float(r["score_final"]), round(0.4 * float(r["score_credibility"]) + 0.3 * float(r["score_golem"]) + 0.3 * float(r["score_vote"]), 2))
        by = {r["idea_id"]: r for r in rows}
        self.assertEqual(float(by["idea-1"]["score_vote"]), 100.0)
        self.assertAlmostEqual(float(by["idea-2"]["score_vote"]), 66.67, places=2)
        self.assertAlmostEqual(float(by["idea-3"]["score_vote"]), 33.33, places=2)
        # creator 2 (verified, 100K followers) gets a far higher credibility than the others
        self.assertGreater(float(by["idea-2"]["score_credibility"]), float(by["idea-1"]["score_credibility"]))

    def test_a_voter_who_sold_before_the_close_does_not_count(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 3)
            sold = acct(100)
            chain.balances[sold.address.lower()] = 5.0           # flash-loan style: held it at vote time, not at the close
            unknown = acct(101)
            chain.balances.pop(unknown.address.lower())           # unreadable at the close: fails closed
            res = await self.score(db, chain)
            votes = {v["voter_wallet"]: v["counted"] for v in await gf_store.votes_of_round(db, D)}
            return res["n_votes"], votes[sold.address.lower()], votes[unknown.address.lower()], votes[acct(102).address.lower()]
        self.assertEqual(run(go), (1, False, False, True))

    def test_the_balance_is_read_again_at_the_close(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 2)
            chain.calls.clear()
            await self.score(db, chain)
            return [c for c in chain.calls if c[0] == "balance"]
        self.assertEqual(len(run(go)), 2)

    def test_no_launch_without_votes_or_without_approved_ideas(self):
        chain = FakeChain()

        async def no_votes(db):
            await seed_pool(db, chain, 2)
            return (await self.score(db, chain))["no_launch_reason"], (await gf_store.get_round(db, D))["winner_idea_id"]
        self.assertEqual(run(no_votes), ("no_votes", None))

        async def no_ideas(db):
            await gf_store.ensure_round(db, D)
            await db.commit()
            return (await self.score(db, chain))["no_launch_reason"]
        self.assertEqual(run(no_ideas), "no_approved_ideas")

    def test_scoring_is_idempotent(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 2)
            first = await self.score(db, chain)
            second = await self.score(db, chain)
            return first is not None, second
        self.assertEqual(run(go), (True, None))

    def test_a_creator_who_won_within_seven_days_is_skipped(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            # creator 1 won three days ago
            old = date(2026, 10, 3)
            await gf_store.ensure_round(db, old)
            await gf_store.insert_idea(db, dict(idea_id="old-win", round_date=old, x_user_id="x1", wallet=acct(1).address.lower(),
                                                name="Old", ticker="OLD", lore=LORE, image_url="/x", image_sha256="b" * 64,
                                                fee_tx="0x" + "bb" * 32, status="approved", reject_reason=None, auto_flags={},
                                                lore_embedding=None, submitted_at=T(9, day=3)))
            await gf_store.set_round(db, old, winner_idea_id="old-win", status="launched", announced_at=T(20, 5, day=3))
            await db.commit()
            await add_votes(db, chain, "idea-1", 100, 5)      # idea-1 would win on votes
            await add_votes(db, chain, "idea-2", 200, 1)
            res = await self.score(db, chain)
            return res["winner"].idea_id, res["skipped"][0][0]
        self.assertEqual(run(go), ("idea-2", "idea-1"))

    def test_meta_radar_cluster_and_saturation_feed_the_golem_score(self):
        chain = FakeChain()
        vec = gf_validation.hash_embedding(LORE)

        async def go(db):
            await db.execute(text("CREATE TABLE IF NOT EXISTS radar_runs (run_id TEXT PRIMARY KEY, run_at TIMESTAMPTZ, baseline_rate NUMERIC)"))
            await db.execute(text("CREATE TABLE IF NOT EXISTS radar_clusters (run_id TEXT, cluster_id TEXT, survival_rate NUMERIC, "
                                  "n_resolved INTEGER, saturation BOOLEAN, centroid_vec DOUBLE PRECISION[])"))
            await db.execute(text("TRUNCATE radar_runs, radar_clusters"))
            await db.execute(text("INSERT INTO radar_runs VALUES ('r1', now(), 0.15)"))
            await db.execute(text("INSERT INTO radar_clusters VALUES ('r1', 'c1', 0.30, 80, false, CAST(:v AS DOUBLE PRECISION[]))"), {"v": vec})
            await db.commit()
            await seed_pool(db, chain, 2)           # idea-1 lore == the centroid, idea-2 lore is unrelated
            unit = lambda i: [1.0 if k == i else 0.0 for k in range(256)]
            await db.execute(text("UPDATE radar_clusters SET centroid_vec = CAST(:v AS DOUBLE PRECISION[])"), {"v": unit(0)})
            await db.execute(text("UPDATE gf_ideas SET lore_embedding = CAST(:v AS DOUBLE PRECISION[]) WHERE idea_id = 'idea-1'"), {"v": unit(0)})
            await db.execute(text("UPDATE gf_ideas SET lore_embedding = CAST(:v AS DOUBLE PRECISION[]) WHERE idea_id = 'idea-2'"), {"v": unit(1)})
            await db.commit()
            await add_votes(db, chain, "idea-1", 100, 1)
            res = await self.score(db, chain)
            rows = {r["idea_id"]: r for r in await gf_store.ideas_of_round(db, D, ["approved"])}
            sat = rows["idea-1"]
            await db.execute(text("UPDATE radar_clusters SET saturation = true"))
            await db.commit()
            return rows, res
        rows, res = run(go)
        i1, i2 = rows["idea-1"], rows["idea-2"]
        self.assertEqual(i1["radar_cluster_id"], "c1")
        self.assertIsNone(i2["radar_cluster_id"])
        # idea-1: lift 2 -> narrative 100; idea-2 fits no cluster -> neutral 50. The lore part is the same curve for both.
        self.assertAlmostEqual(float(i1["score_golem"]), (20 / 30) * 100 + (10 / 30) * lore_q(LORE), places=1)
        self.assertAlmostEqual(float(i2["score_golem"]), (20 / 30) * 50 + (10 / 30) * lore_q(LORE2), places=1)

    def test_a_saturated_narrative_loses_20_points(self):
        chain = FakeChain()
        vec = gf_validation.hash_embedding(LORE)

        async def go(db):
            await db.execute(text("CREATE TABLE IF NOT EXISTS radar_runs (run_id TEXT PRIMARY KEY, run_at TIMESTAMPTZ, baseline_rate NUMERIC)"))
            await db.execute(text("CREATE TABLE IF NOT EXISTS radar_clusters (run_id TEXT, cluster_id TEXT, survival_rate NUMERIC, "
                                  "n_resolved INTEGER, saturation BOOLEAN, centroid_vec DOUBLE PRECISION[])"))
            await db.execute(text("TRUNCATE radar_runs, radar_clusters"))
            await db.execute(text("INSERT INTO radar_runs VALUES ('r1', now(), 0.15)"))
            await db.execute(text("INSERT INTO radar_clusters VALUES ('r1', 'c1', 0.30, 80, true, CAST(:v AS DOUBLE PRECISION[]))"), {"v": vec})
            await db.commit()
            await seed_pool(db, chain, 1)
            await add_votes(db, chain, "idea-1", 100, 1)
            await self.score(db, chain)
            return float((await gf_store.get_idea(db, "idea-1"))["score_golem"])
        self.assertAlmostEqual(run(go), (20 / 30) * 100 + (10 / 30) * lore_q(LORE) - 20.0, places=1)

    def test_missing_radar_tables_mean_neutral_scores_not_a_crash(self):
        chain = FakeChain()

        async def go(db):
            await db.execute(text("DROP TABLE IF EXISTS radar_clusters"))
            await db.execute(text("DROP TABLE IF EXISTS radar_runs"))
            await db.commit()
            await seed_pool(db, chain, 1)
            await add_votes(db, chain, "idea-1", 100, 1)
            await self.score(db, chain)
            return float((await gf_store.get_idea(db, "idea-1"))["score_golem"])
        self.assertAlmostEqual(run(go), (20 / 30) * 50 + (10 / 30) * lore_q(LORE), places=1)

    def test_announcement_waits_for_20_05_then_schedules_the_launch(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 2)
            await self.score(db, chain)
            d = deps(chain, self.tmp.name)
            early = await svc.announce_round(db, d, D, T(20, 4))
            out = await svc.announce_round(db, d, D, ANNOUNCE_AT)
            again = await svc.announce_round(db, d, D, ANNOUNCE_AT + timedelta(minutes=1))
            rnd = await gf_store.get_round(db, D)
            slot = await gf_store.get_launch(db, out["idea"]["idea_id"])
            creator = await gf_store.creator_by_x(db, "x1")
            return early, again, out, rnd, slot, creator
        early, again, out, rnd, slot, creator = run(go)
        self.assertEqual((early, again), (None, None))
        self.assertEqual(out["kind"], "winner")
        self.assertEqual((rnd["status"], rnd["announced_at"]), ("announced", ANNOUNCE_AT))
        self.assertIsNotNone(slot["launch_due_at"])
        self.assertLessEqual(slot["launch_due_at"], ANNOUNCE_AT + timedelta(hours=24))
        self.assertGreaterEqual(slot["launch_due_at"], ANNOUNCE_AT + timedelta(minutes=30))
        self.assertEqual(slot["launch_due_at"].minute, 0)
        self.assertIn("survival", slot["launch_hour_reason"])
        self.assertEqual(creator["last_win_at"], ANNOUNCE_AT)

    def test_no_launch_is_announced_with_its_reason(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 1)
            await self.score(db, chain)
            out = await svc.announce_round(db, deps(chain, self.tmp.name), D, ANNOUNCE_AT)
            body = await svc.winner_post_text(db, out)
            return out["kind"], out["reason"], (await gf_store.get_round(db, D))["status"], body
        kind, reason, status, body = run(go)
        self.assertEqual((kind, reason, status), ("no_launch", "no_votes", "no_launch"))
        self.assertIn("no launch today", body.lower())
        self.assertIn("eligible vote", body)

    def test_the_winner_post_matches_the_brief_and_has_no_dollar_sign(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 2)
            await self.score(db, chain)
            out = await svc.announce_round(db, deps(chain, self.tmp.name), D, ANNOUNCE_AT)
            return await svc.winner_post_text(db, out)
        body = run(go)
        self.assertNotIn("$", body)
        self.assertIn("GoForge winner, 2026-10-06", body)
        self.assertIn("by @creator1", body)
        self.assertIn("2 ideas submitted · 2 votes cast", body)
        self.assertIn("50% creator, 50% EPC buyback & burn", body)      # the compact wording that fits 280 characters
        self.assertIn("epochlabs.run/goforge", body)

    def test_a_round_left_open_by_an_outage_can_still_be_scored(self):
        chain = FakeChain()

        async def go(db):
            await seed_pool(db, chain, 2)
            await add_votes(db, chain, "idea-1", 100, 2)
            stuck = await gf_store.open_rounds_before(db, D + timedelta(days=1))
            res = await svc.score_round(db, deps(chain, self.tmp.name), D, T(3, day=7))
            return [r["round_date"] for r in stuck], res["winner"].idea_id
        self.assertEqual(run(go), ([D], "idea-1"))


def lore_q(s):
    return gf_scoring.lore_quality(len(s))


# ---------------------------------------------------------------------------
# Launch, verdict, distributions, posts
# ---------------------------------------------------------------------------

CA = "0x" + "ca" * 20
SPLITTER = "0x" + "5b" * 20
LTX = "0x" + "aa" * 32


async def winner_round(db, chain, dep):
    await seed_pool(db, chain, 2)
    await add_votes(db, chain, "idea-1", 100, 2)
    await svc.score_round(db, dep, D, CLOSE_AT)
    return await svc.announce_round(db, dep, D, ANNOUNCE_AT)


class TestLaunchAndAfter(PgCase):
    def reg(self, db, chain, **kw):
        args = dict(idea_id="idea-1", ca=CA, launch_tx=LTX, splitter=SPLITTER, registered_by="team:a", now=T(21))
        args.update(kw)
        return svc.register_launch(db, deps(chain, self.tmp.name), **args)

    def test_registration_validates_and_activates_tracking_and_exclusion(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            out = await self.reg(db, chain)
            slot = await gf_store.get_launch(db, "idea-1")
            rnd = await gf_store.get_round(db, D)
            g = (await db.execute(text("SELECT id, ca, launch_tx FROM goforge_launches"))).all()
            return out, slot, rnd, [tuple(r) for r in g]
        out, slot, rnd, g = run(go)
        self.assertEqual((slot["ca"], slot["launch_tx"], slot["splitter_address"], rnd["status"]), (CA, LTX, SPLITTER, "launched"))
        self.assertEqual(slot["dist_block"], 990)
        self.assertEqual(g, [("idea-1", CA, LTX)])
        # the watcher now tracks it, and Golem's trading never touches it
        self.assertIn(CA, goforge_config.goforge_cas())
        self.assertIn(CA, settings.desk_excluded_tokens)
        self.assertIn("idea-1", [e.id for e in goforge_config.all_entries()])

    def test_registration_rules(self):
        async def go(db, chain, expect, **kw):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            with self.assertRaises(svc.ServiceError) as ctx:
                await self.reg(db, chain, **kw)
            self.assertEqual(ctx.exception.code, expect, kw)
            self.assertNotIn(CA, goforge_config.goforge_cas())      # a refused registration excludes nothing

        base = FakeChain()
        base.codes = {CA: True, SPLITTER: True}
        for expect, kw, chain in (
            ("invalid_registration", dict(ca="0x123"), base),
            ("invalid_registration", dict(launch_tx="0xabc"), base),
            ("invalid_registration", dict(splitter="nope"), base),
            ("no_launch_slot", dict(idea_id="idea-2"), base),
            ("chain_unavailable", {}, FakeChain()),                                       # codes unknown -> cannot verify
            ("not_a_contract", {}, _chain(codes={CA: False, SPLITTER: True})),
            ("not_a_contract", {}, _chain(codes={CA: True, SPLITTER: False})),
        ):
            with self.subTest(expect=expect, kw=kw):
                run(lambda db, c=chain, e=expect, k=kw: go(db, c, e, **k))

    def test_a_launch_cannot_be_registered_twice(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True, "0x" + "cb" * 20: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)
            await self.reg(db, chain, ca="0x" + "cb" * 20)
        async def main(db):
            with self.assertRaises(svc.ServiceError) as ctx:
                await go(db)
            return ctx.exception.code
        self.assertEqual(run(main), "already_registered")

    def test_registered_launches_are_reloaded_after_a_restart(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)
            goforge_config.clear_runtime_entries()                       # the process restarted
            entries = await svc.load_registered_entries(db)
            for e in entries:
                goforge_config.register_runtime_entry(e)
            return [(e.id, e.ca, e.why["why_hash"]) for e in entries], (await gf_store.get_round(db, D))["scoreboard_sha256"]
        entries, sha = run(go)
        self.assertEqual(entries, [("idea-1", CA, sha)])
        self.assertIn(CA, goforge_config.goforge_cas())

    def test_the_verdict_is_copied_once_and_updates_the_creator_track_record(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)
            none_yet = await svc.sync_verdicts(db, deps(chain, self.tmp.name))
            await db.execute(text("UPDATE goforge_launches SET verdict = 'reached_30k', verdict_at = now(), peak_mc_usd = 41000 WHERE id = 'idea-1'"))
            await db.commit()
            first = await svc.sync_verdicts(db, deps(chain, self.tmp.name))
            second = await svc.sync_verdicts(db, deps(chain, self.tmp.name))
            creator = await gf_store.creator_by_x(db, "x1")
            launch = await gf_store.get_launch(db, "idea-1")
            return none_yet, first, second, creator, launch
        none_yet, first, second, creator, launch = run(go)
        self.assertEqual((none_yet, second), ([], []))
        self.assertEqual((first[0]["verdict"], first[0]["peak_mc_usd"]), ("reached_30k", 41000))
        self.assertEqual((creator["wins_reached_30k"], creator["wins_stalled"]), (1, 0))
        self.assertEqual((launch["verdict"], float(launch["peak_mc_usd"]), launch["verdict_synced"]), ("reached_30k", 41000.0, True))

    def test_a_stalled_verdict_counts_against_the_creator(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)
            await db.execute(text("UPDATE goforge_launches SET verdict = 'stalled', verdict_at = now(), peak_mc_usd = 9000 WHERE id = 'idea-1'"))
            await db.commit()
            await svc.sync_verdicts(db, deps(chain, self.tmp.name))
            c = await gf_store.creator_by_x(db, "x1")
            return c["wins_reached_30k"], c["wins_stalled"]
        self.assertEqual(run(go), (0, 1))

    def test_fee_distributions_are_indexed_once_and_summed(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}
        chain.dist[SPLITTER] = [
            dict(tx_hash="0x" + "d1" * 32, log_index=0, block=1020, at=T(22), creator_wei=10 ** 18, burn_wei=10 ** 18, epc_burned_wei=4000 * 10 ** 18),
            dict(tx_hash="0x" + "d2" * 32, log_index=3, block=1050, at=T(23), creator_wei=5 * 10 ** 17, burn_wei=5 * 10 ** 17, epc_burned_wei=1000 * 10 ** 18),
            dict(tx_hash="0x" + "d3" * 32, log_index=0, block=900, at=T(10), creator_wei=10 ** 20, burn_wei=1, epc_burned_wei=1),   # before the launch
        ]

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)                    # registered when the chain head was 1000: the cursor starts at 990
            chain.head = 1100
            first = await svc.index_distributions(db, deps(chain, self.tmp.name))
            second = await svc.index_distributions(db, deps(chain, self.tmp.name))
            launch = await gf_store.get_launch(db, "idea-1")
            return first, second, launch
        first, second, launch = run(go)
        self.assertEqual([d["tx_hash"][-4:] for d in first], ["d1d1", "d2d2"])
        self.assertEqual(second, [])
        self.assertEqual((float(launch["fees_to_creator"]), float(launch["epc_burned"])), (1.5, 5000.0))
        self.assertEqual(launch["dist_block"], 1101)

    def test_the_keeper_only_calls_splitters_that_hold_eth_and_are_due(self):
        chain = FakeChain()
        chain.codes = {CA: True, SPLITTER: True}

        async def go(db):
            await winner_round(db, chain, deps(chain, self.tmp.name))
            await self.reg(db, chain)
            d = deps(chain, self.tmp.name)
            off = await svc.keeper_distribute(db, d, T(22))
            with mock.patch.object(settings, "GF_KEEPER_PRIVATE_KEY", key(7)):
                empty = await svc.keeper_distribute(db, d, T(22))          # no ETH in the splitter
                chain.eth[SPLITTER] = 10 ** 18
                sent = await svc.keeper_distribute(db, d, T(22))
                too_soon = await svc.keeper_distribute(db, d, T(23))      # distributed an hour ago
                later = await svc.keeper_distribute(db, d, T(22, day=7, ) + timedelta(hours=1))
            return off, empty, sent, too_soon, later
        off, empty, sent, too_soon, later = run(go)
        self.assertEqual((off, empty, too_soon), ([], [], []))
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(later), 1)


def _chain(codes):
    c = FakeChain()
    c.codes = codes
    return c


class TestPosts(PgCase):
    class Poster:
        def __init__(self, results):
            self.results, self.sent = list(results), []

        async def post_desk_tweet(self, body, trigger):
            self.sent.append((body, trigger))
            return self.results.pop(0)

    def test_dry_run_unless_x_posting_is_enabled(self):
        chain = FakeChain()

        async def go(db):
            poster = self.Poster([])
            status = await svc.post_once(db, deps(chain, self.tmp.name, poster=poster), "winner", "2026-10-06", "hello")
            return status, poster.sent
        self.assertEqual(run(go), ("dry_run", []))

    def test_a_post_is_sent_once_even_if_asked_twice(self):
        chain = FakeChain()

        async def go(db):
            poster = self.Poster([{"status": "sent", "tweet_id": "42"}, {"status": "sent", "tweet_id": "43"}])
            d = deps(chain, self.tmp.name, poster=poster)
            with mock.patch.object(settings, "GF_X_POST_ENABLED", True):
                a = await svc.post_once(db, d, "winner", "2026-10-06", "hello")
                b = await svc.post_once(db, d, "winner", "2026-10-06", "hello again")
            row = (await db.execute(text("SELECT status, tweet_id, text FROM gf_posts"))).all()
            return a, b, len(poster.sent), [tuple(r) for r in row], poster.sent[0][1]
        a, b, n, row, trigger = run(go)
        self.assertEqual((a, b), ("posted", "duplicate"))
        self.assertEqual(n, 1)          # the second caller found the post already claimed and sent nothing
        self.assertEqual(row, [("posted", "42", "hello")])

    def test_failures_are_retried_up_to_three_times(self):
        chain = FakeChain()

        async def go(db):
            poster = self.Poster([{"status": "failed", "error_message": "429"}] * 5)
            d = deps(chain, self.tmp.name, poster=poster)
            with mock.patch.object(settings, "GF_X_POST_ENABLED", True):
                await svc.post_once(db, d, "live", "idea-1", "text")
                for _ in range(5):
                    await svc.retry_failed_posts(db, d)
            row = (await db.execute(text("SELECT status, attempts, error FROM gf_posts"))).first()
            return tuple(row), len(poster.sent)
        row, sent = run(go)
        self.assertEqual(row, ("failed", 3, "429"))
        self.assertEqual(sent, 3)

    def test_a_retry_that_succeeds_ends_the_queue(self):
        chain = FakeChain()

        async def go(db):
            poster = self.Poster([{"status": "failed", "error_message": "boom"}, {"status": "sent", "tweet_id": "9"}])
            d = deps(chain, self.tmp.name, poster=poster)
            with mock.patch.object(settings, "GF_X_POST_ENABLED", True):
                await svc.post_once(db, d, "verdict", "idea-1", "text")
                n = await svc.retry_failed_posts(db, d)
                n2 = await svc.retry_failed_posts(db, d)
            return n, n2, tuple((await db.execute(text("SELECT status, tweet_id FROM gf_posts"))).first())
        self.assertEqual(run(go), (1, 0, ("posted", "9")))


if __name__ == "__main__":
    unittest.main()
