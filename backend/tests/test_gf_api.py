import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

import gf_pg
from app.api import gf_endpoints as api
from app.core import goforge_config
from app.core.config import settings
from app.db.database import get_db
from app.services import gf_identity, gf_store, gf_validation
from app.services import gf_service as svc
from app.services.chain_reader import TOPIC_TRANSFER, address_topic
from app.services import gf_chain
from test_gf_service import FakeChain, X, acct, png, sign_vote, FEE_WEI, LORE, LORE2

D = date(2026, 10, 6)
SECRET = "test-secret-" + "x" * 40
ADMIN = "admin-token-" + "y" * 30


def T(hour, minute=0, day=6):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


async def override_db():
    async with gf_pg.session() as db:
        yield db


class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gf_pg.setup_schema()
        cls.tmp = tempfile.TemporaryDirectory()
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[get_db] = override_db
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        asyncio.run(gf_pg.truncate_all())
        goforge_config.clear_runtime_entries()
        self.addCleanup(goforge_config.clear_runtime_entries)
        self.chain = FakeChain()
        self.clock = Clock(T(9))
        self.deps = svc.Deps(chain=self.chain, embed=lambda ts: ([gf_validation.hash_embedding(t) for t in ts], "test"),
                             image_dir=Path(self.tmp.name), blocklists=gf_validation.load_blocklists())
        self.broadcasts = []

        async def fake_broadcast(msg):
            self.broadcasts.append(msg)
        patches = [
            mock.patch.object(api, "_deps", self.deps), mock.patch.object(api, "now_utc", self.clock),
            mock.patch.object(settings, "GF_SESSION_SECRET", SECRET), mock.patch.object(settings, "GF_ADMIN_TOKEN", ADMIN),
            mock.patch.object(settings, "GF_FRONTEND_URL", "http://localhost:3000/goforge"),
            mock.patch.object(settings, "GF_SIWE_DOMAIN", "epochlabs.run"),
            mock.patch.object(api.manager, "broadcast", fake_broadcast),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        api.invalidate_cache()
        self.client = TestClient(self.app, follow_redirects=False)

    # --- helpers ----------------------------------------------------------
    def login(self, account, client=None):
        client = client or self.client
        n = client.get("/api/goforge/auth/nonce").json()
        msg = gf_identity.build_siwe_message(domain=n["domain"], address=account.address, uri=n["uri"], chain_id=n["chain_id"],
                                             nonce=n["nonce"], issued_at=self.clock.now, statement=n["statement"])
        sig = Account.sign_message(encode_defunct(text=msg), account.key).signature.hex()
        return client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig}), msg, sig

    def run_db(self, coro_fn):
        async def go():
            async with gf_pg.session() as db:
                return await coro_fn(db)
        return asyncio.run(go())

    def link_x(self, n, handle=None):
        a = acct(n)

        async def go(db):
            await gf_store.upsert_creator(db, X(f"x{n}", handle or f"user{n}"), a.address)
            await db.commit()
        self.run_db(go)
        return a

    def idea_form(self, **kw):
        base = dict(name="Spore Keeper", ticker="SPORE", lore=LORE, fee_tx="0x" + "01" * 32)
        base.update(kw)
        return base

    def post_idea(self, client=None, image=None, **kw):
        client = client or self.client
        return client.post("/api/goforge/ideas", data=self.idea_form(**kw),
                           files={"image": ("idea.png", image or png(), "image/png")})


class TestRoundEndpoint(ApiCase):
    def test_submit_phase_hides_the_pool_and_shows_the_countdown_and_rules(self):
        a = self.link_x(1)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        self.login(a)
        self.assertEqual(self.post_idea().status_code, 200)
        self.run_db(lambda db: self._approve(db))
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual((body["phase"], body["round_date"], body["pool_visible"], body["ideas"]), ("submit", "2026-10-06", False, []))
        self.assertEqual(body["slots"], {"used": 1, "max": 100})
        self.assertEqual(body["next"]["label"], "submit_closes")
        self.assertEqual(body["next"]["at"], T(12).isoformat())
        self.assertEqual(body["rules"]["submit_fee_epc"], settings.GF_SUBMIT_FEE_EPC)
        self.assertEqual(body["rules"]["weights"], {"credibility": 0.4, "golem": 0.3, "vote": 0.3})
        self.assertTrue(body["rules"]["login_configured"])
        self.assertIsNone(body["winner"])

    async def _approve(self, db):
        row = (await db.execute(text("SELECT idea_id FROM gf_ideas LIMIT 1"))).first()
        await gf_store.review_idea(db, row[0], "approved", None, "t")
        await db.commit()

    def test_vote_phase_shows_the_approved_pool_with_vote_counts_and_no_private_fields(self):
        a = self.link_x(1)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        self.login(a)
        self.post_idea()
        self.run_db(lambda db: self._approve(db))
        self.clock.now = T(15)
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual((body["phase"], body["pool_visible"], len(body["ideas"])), ("vote", True, 1))
        idea = body["ideas"][0]
        self.assertEqual((idea["name"], idea["ticker"], idea["creator_handle"], idea["votes"]), ("Spore Keeper", "SPORE", "user1", 0))
        blob = json.dumps(body)
        for secret in ('"wallet"', '"fee_tx"', '"auto_flags"', '"lore_embedding"', '"x_user_id"', acct(1).address.lower()):
            self.assertNotIn(secret, blob)

    def test_pending_and_rejected_ideas_are_never_in_the_pool(self):
        a, b = self.link_x(1), self.link_x(2)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        self.chain.add_fee_tx("0x" + "02" * 32, b.address)
        self.login(a)
        self.post_idea()
        self.login(b)
        self.post_idea(name="Moss Keeper", ticker="MOSS", lore=LORE2, fee_tx="0x" + "02" * 32)
        self.clock.now = T(15)
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual(body["ideas"], [])           # both still wait for the team
        self.assertEqual(body["slots"]["used"], 2)

    def test_the_response_is_cached_briefly_and_invalidated_by_a_vote(self):
        self.client.get("/api/goforge/round")
        with mock.patch.object(api.gf_store, "count_ideas", side_effect=AssertionError("served from the DB, not the cache")):
            self.client.get("/api/goforge/round")      # within 5 s: cache
        api.invalidate_cache()
        with mock.patch.object(api.gf_store, "count_ideas", return_value=7):
            self.assertEqual(self.client.get("/api/goforge/round").json()["slots"]["used"], 7)

    def test_the_winner_and_the_totals_appear_after_the_announcement(self):
        async def seed(db):
            from test_gf_service import seed_pool, add_votes
            await seed_pool(db, self.chain, 2)
            await add_votes(db, self.chain, "idea-1", 100, 2)
            await svc.score_round(db, self.deps, D, T(20))
            await svc.announce_round(db, self.deps, D, T(20, 5))
        self.run_db(seed)
        self.clock.now = T(21)
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual((body["phase"], body["status"]), ("announced", "announced"))
        self.assertEqual(body["winner"]["idea_id"], "idea-1")
        self.assertEqual(set(body["winner"]["scores"]), {"credibility", "golem", "vote", "final"})
        self.assertEqual(body["n_votes"], 2)
        self.assertTrue(body["scoreboard_available"])
        self.assertEqual(body["totals"]["ideas_submitted"], 2)
        self.assertNotIn("launch_due_at", json.dumps(body))      # the launch hour is not published: it would invite snipers

    def test_the_winner_is_not_shown_while_the_round_is_still_scoring(self):
        async def seed(db):
            from test_gf_service import seed_pool, add_votes
            await seed_pool(db, self.chain, 2)
            await add_votes(db, self.chain, "idea-1", 100, 2)
            await svc.score_round(db, self.deps, D, T(20))
        self.run_db(seed)
        self.clock.now = T(20, 2)
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual((body["phase"], body["winner"]), ("scoring", None))

    def test_last_round_summary_and_no_launch_reason(self):
        async def seed(db):
            from test_gf_service import seed_pool
            await seed_pool(db, self.chain, 1)
            await svc.score_round(db, self.deps, D, T(20))
            await svc.announce_round(db, self.deps, D, T(20, 5))
        self.run_db(seed)
        self.clock.now = T(1, day=7)
        api.invalidate_cache()
        body = self.client.get("/api/goforge/round").json()
        self.assertEqual((body["round_date"], body["phase"]), ("2026-10-07", "submit"))
        self.assertEqual((body["last_round"]["status"], body["last_round"]["no_launch_reason"], body["last_round"]["winner"]), ("no_launch", "no_votes", None))


class TestAuth(ApiCase):
    def test_siwe_login_sets_an_httponly_session_and_me_works(self):
        a = acct(1)
        r, _, _ = self.login(a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["wallet"], a.address.lower())
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("gf_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        me = self.client.get("/api/goforge/auth/me").json()
        self.assertEqual((me["wallet"], me["x"], me["is_team"], me["submitted_today"]), (a.address.lower(), None, False, False))

    def test_the_nonce_endpoint_hands_back_the_checksummed_address_wallets_insist_on(self):
        a = acct(1)
        lower = a.address.lower()
        n = self.client.get("/api/goforge/auth/nonce", params={"address": lower}).json()
        self.assertEqual(n["address"], a.address)                         # EIP-55, not the lower-case the wallet gave
        self.assertNotEqual(n["address"], lower)
        # a message built with that address (what a wallet will accept) logs in
        msg = gf_identity.build_siwe_message(domain=n["domain"], address=n["address"], uri=n["uri"], chain_id=n["chain_id"],
                                             nonce=n["nonce"], issued_at=self.clock.now, statement=n["statement"])
        sig = Account.sign_message(encode_defunct(text=msg), a.key).signature.hex()
        r = self.client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig})
        self.assertEqual((r.status_code, r.json()["wallet"]), (200, lower))
        self.assertIsNone(self.client.get("/api/goforge/auth/nonce").json()["address"])
        self.assertEqual(self.client.get("/api/goforge/auth/nonce", params={"address": "0x12"}).status_code, 422)

    def test_the_vote_typed_data_names_the_voter_in_checksum_form(self):
        v = acct(11)
        typed = self.client.get("/api/goforge/vote/typed-data", params={"voter": v.address.lower(), "idea_id": "i"}).json()
        self.assertEqual(typed["message"]["voter"], v.address)
        self.assertEqual(self.client.get("/api/goforge/vote/typed-data", params={"voter": "nope", "idea_id": "i"}).status_code, 422)

    def test_a_nonce_works_once(self):
        a = acct(1)
        _, msg, sig = self.login(a)
        again = self.client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig})
        self.assertEqual((again.status_code, again.json()["detail"]["code"]), (401, "bad_nonce"))

    def test_an_unknown_nonce_is_refused(self):
        a = acct(1)
        n = self.client.get("/api/goforge/auth/nonce").json()
        msg = gf_identity.build_siwe_message(domain=n["domain"], address=a.address, uri=n["uri"], chain_id=n["chain_id"],
                                             nonce="deadbeef" * 4, issued_at=self.clock.now)
        sig = Account.sign_message(encode_defunct(text=msg), a.key).signature.hex()
        r = self.client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (401, "bad_nonce"))

    def test_a_signature_from_another_wallet_is_refused(self):
        a, b = acct(1), acct(2)
        n = self.client.get("/api/goforge/auth/nonce").json()
        msg = gf_identity.build_siwe_message(domain=n["domain"], address=a.address, uri=n["uri"], chain_id=n["chain_id"],
                                             nonce=n["nonce"], issued_at=self.clock.now)
        sig = Account.sign_message(encode_defunct(text=msg), b.key).signature.hex()       # b signs a message that names a
        r = self.client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (401, "bad_signature"))
        self.assertNotIn("set-cookie", r.headers)

    def test_wrong_domain_is_refused(self):
        a = acct(1)
        n = self.client.get("/api/goforge/auth/nonce").json()
        msg = gf_identity.build_siwe_message(domain="evil.example", address=a.address, uri=n["uri"], chain_id=n["chain_id"],
                                             nonce=n["nonce"], issued_at=self.clock.now)
        sig = Account.sign_message(encode_defunct(text=msg), a.key).signature.hex()
        self.assertEqual(self.client.post("/api/goforge/auth/siwe", json={"message": msg, "signature": sig}).status_code, 401)

    def test_login_is_off_without_a_session_secret(self):
        with mock.patch.object(settings, "GF_SESSION_SECRET", ""):
            self.assertEqual(self.client.get("/api/goforge/auth/nonce").status_code, 503)
            self.assertEqual(self.client.post("/api/goforge/auth/siwe", json={"message": "x", "signature": "y"}).status_code, 503)
            self.assertIsNone(self.client.get("/api/goforge/auth/me").json()["wallet"])

    def test_logout_and_anonymous_me(self):
        self.login(acct(1))
        self.client.post("/api/goforge/auth/logout")
        self.assertIsNone(self.client.get("/api/goforge/auth/me").json()["wallet"])
        self.assertIsNone(TestClient(self.app).get("/api/goforge/auth/me").json()["wallet"])

    def test_a_tampered_cookie_is_not_a_session(self):
        self.client.cookies.set("gf_session", "not-a-jwt")
        self.assertIsNone(self.client.get("/api/goforge/auth/me").json()["wallet"])

    def test_bearer_token_also_works(self):
        a = acct(1)
        token = gf_identity.issue_session(SECRET, a.address, datetime.now(timezone.utc), 24)
        fresh = TestClient(self.app)
        me = fresh.get("/api/goforge/auth/me", headers={"authorization": f"Bearer {token}"}).json()
        self.assertEqual(me["wallet"], a.address.lower())

    def test_me_reports_the_linked_x_account_the_team_flag_and_the_current_vote(self):
        a = self.link_x(1, "sporefan")

        async def seed(db):
            await gf_store.ensure_round(db, D)
            await gf_store.insert_idea(db, dict(idea_id="i1", round_date=D, x_user_id="x1", wallet="0x" + "9" * 40, name="n", ticker="TK", lore="l" * 60,
                                                image_url="/x", image_sha256="a" * 64, fee_tx="0x" + "e" * 64, status="approved", reject_reason=None,
                                                auto_flags={}, lore_embedding=None, submitted_at=T(8)))
            await gf_store.upsert_vote(db, D, a.address, "i1", "sig", 1.0, 1)
            await db.commit()
        self.run_db(seed)
        self.login(a)
        me = self.client.get("/api/goforge/auth/me").json()
        self.assertEqual(me["x"]["handle"], "sporefan")
        self.assertEqual(me["my_vote"], {"round_date": "2026-10-06", "idea_id": "i1"})
        with mock.patch.object(settings, "GF_TEAM_WALLETS", a.address):
            self.assertTrue(self.client.get("/api/goforge/auth/me").json()["is_team"])


class FakeXClient:
    def __init__(self, user=None, token_status=200):
        self.user, self.token_status = user, token_status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        class R:
            status_code = self.token_status

            def json(s):
                return {"access_token": "tok"}
        return R()

    async def get(self, url, **kw):
        user = self.user

        class R:
            status_code = 200

            def json(s):
                return {"data": {"id": user["x_user_id"], "username": user["x_handle"], "verified": user["x_verified"],
                                 "created_at": "2019-04-02T10:00:00.000Z", "public_metrics": {"followers_count": user["x_followers"]}}}
        return R()


class TestXLogin(ApiCase):
    X_ENV = dict(GF_X_CLIENT_ID="cid", GF_X_CLIENT_SECRET="sec", GF_X_REDIRECT_URI="http://localhost:8000/api/goforge/auth/x/callback")

    @contextmanager
    def x_configured(self, fake=None):
        with mock.patch.object(settings, "GF_X_CLIENT_ID", "cid"), mock.patch.object(settings, "GF_X_CLIENT_SECRET", "sec"), \
             mock.patch.object(settings, "GF_X_REDIRECT_URI", self.X_ENV["GF_X_REDIRECT_URI"]):
            if fake is not None:
                async def make():
                    return fake
                with mock.patch.object(api, "x_client", make):
                    yield
            else:
                yield

    def start(self, account):
        self.login(account)
        r = self.client.get("/api/goforge/auth/x")
        self.assertEqual(r.status_code, 302)
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(r.headers["location"]).query)["state"][0]

    def test_start_needs_a_wallet_session_and_configuration(self):
        self.assertEqual(TestClient(self.app).get("/api/goforge/auth/x").status_code, 401)
        self.login(acct(1))
        self.assertEqual(self.client.get("/api/goforge/auth/x").status_code, 503)      # not configured

    def test_start_redirects_to_x_with_pkce(self):
        with self.x_configured():
            self.login(acct(1))
            r = self.client.get("/api/goforge/auth/x")
        self.assertEqual(r.status_code, 302)
        loc = r.headers["location"]
        self.assertTrue(loc.startswith("https://x.com/i/oauth2/authorize?"))
        for needle in ("code_challenge_method=S256", "response_type=code", "client_id=cid", "state="):
            self.assertIn(needle, loc)

    def test_callback_links_the_account_from_the_x_api_not_from_the_client(self):
        a = acct(1)
        user = dict(x_user_id="777", x_handle="RealHandle", x_verified=True, x_followers=4242)
        with self.x_configured(FakeXClient(user)):
            state = self.start(a)
            r = self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={state}")
        self.assertEqual(r.status_code, 302)
        self.assertIn("x=linked", r.headers["location"])
        me = self.client.get("/api/goforge/auth/me").json()
        self.assertEqual((me["x"]["handle"], me["x"]["verified"], me["x"]["followers"]), ("RealHandle", True, 4242))
        row = self.run_db(lambda db: gf_store.creator_by_x(db, "777"))
        self.assertEqual(row["wallet"], a.address.lower())

    def test_the_state_is_single_use(self):
        a = acct(1)
        user = dict(x_user_id="777", x_handle="h", x_verified=False, x_followers=1)
        with self.x_configured(FakeXClient(user)):
            state = self.start(a)
            self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={state}")
            again = self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={state}")
        self.assertIn("x_error=expired", again.headers["location"])

    def test_a_forged_state_or_a_cancelled_login(self):
        with self.x_configured(FakeXClient()):
            self.login(acct(1))
            self.assertIn("x_error=expired", self.client.get("/api/goforge/auth/x/callback?code=abc&state=forged").headers["location"])
            self.assertIn("x_error=cancelled", self.client.get("/api/goforge/auth/x/callback?error=access_denied&state=s").headers["location"])
            self.assertIn("x_error=cancelled", self.client.get("/api/goforge/auth/x/callback").headers["location"])

    def test_the_browser_that_finishes_must_be_the_one_that_started(self):
        a = acct(1)
        user = dict(x_user_id="777", x_handle="h", x_verified=False, x_followers=1)
        with self.x_configured(FakeXClient(user)):
            state = self.start(a)
            other = TestClient(self.app, follow_redirects=False)
            self.login(acct(2), other)
            r = other.get(f"/api/goforge/auth/x/callback?code=abc&state={state}")
        self.assertIn("x_error=session_mismatch", r.headers["location"])
        self.assertIsNone(self.run_db(lambda db: gf_store.creator_by_x(db, "777")))

    def test_an_x_account_cannot_be_hijacked_by_a_second_wallet(self):
        first, second = acct(1), acct(2)
        user = dict(x_user_id="777", x_handle="h", x_verified=False, x_followers=1)
        with self.x_configured(FakeXClient(user)):
            s1 = self.start(first)
            self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={s1}")
            other = TestClient(self.app, follow_redirects=False)
            self.login(second, other)
            r = other.get("/api/goforge/auth/x")
            from urllib.parse import parse_qs, urlparse
            s2 = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
            r = other.get(f"/api/goforge/auth/x/callback?code=abc&state={s2}")
        self.assertIn("x_error=already_linked", r.headers["location"])
        self.assertEqual(self.run_db(lambda db: gf_store.creator_by_x(db, "777"))["wallet"], first.address.lower())

    def test_a_wallet_cannot_take_a_second_x_account(self):
        a = acct(1)
        with self.x_configured(FakeXClient(dict(x_user_id="777", x_handle="h", x_verified=False, x_followers=1))):
            s = self.start(a)
            self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={s}")
        with self.x_configured(FakeXClient(dict(x_user_id="888", x_handle="h2", x_verified=False, x_followers=1))):
            r = self.client.get("/api/goforge/auth/x")
            from urllib.parse import parse_qs, urlparse
            s2 = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
            r = self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={s2}")
        self.assertIn("x_error=already_linked", r.headers["location"])

    def test_team_accounts_cannot_link(self):
        a = acct(1)
        with self.x_configured(FakeXClient(dict(x_user_id="777", x_handle="h", x_verified=False, x_followers=1))), \
             mock.patch.object(settings, "GF_TEAM_X_USER_IDS", "777"):
            s = self.start(a)
            r = self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={s}")
        self.assertIn("x_error=team", r.headers["location"])

    def test_x_rejecting_the_code(self):
        a = acct(1)
        with self.x_configured(FakeXClient(token_status=400)):
            s = self.start(a)
            r = self.client.get(f"/api/goforge/auth/x/callback?code=abc&state={s}")
        self.assertIn("x_error=x_rejected", r.headers["location"])


class TestIdeasEndpoint(ApiCase):
    def test_submitting_needs_a_wallet_session(self):
        r = self.post_idea(client=TestClient(self.app))
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (401, "login_required"))

    def test_submit_and_my_ideas_include_the_status_and_the_rejection_reason(self):
        a = self.link_x(1)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        ok = self.post_idea()
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["status"], "pending_review")
        b = self.link_x(2)
        other = TestClient(self.app, follow_redirects=False)
        self.login(b, other)
        self.chain.add_fee_tx("0x" + "02" * 32, b.address)
        rej = self.post_idea(client=other, name="NVIDIA Moon", ticker="NVM", fee_tx="0x" + "02" * 32, lore=LORE2)
        self.assertEqual(rej.json()["status"], "rejected")
        mine = other.get("/api/goforge/ideas/mine").json()["ideas"]
        self.assertEqual((mine[0]["status"], "brand" in mine[0]["reject_reason"].lower()), ("rejected", True))
        self.assertEqual(self.client.get("/api/goforge/ideas/mine").json()["ideas"][0]["reject_reason"], None)
        self.assertNotIn("wallet", json.dumps(mine))

    def test_the_wallet_comes_from_the_session_not_the_form(self):
        a, b = self.link_x(1), self.link_x(2)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        r = self.client.post("/api/goforge/ideas", data={**self.idea_form(), "wallet": b.address, "x_handle": "someone_else"},
                             files={"image": ("i.png", png(), "image/png")})
        self.assertEqual(r.status_code, 200)
        row = self.run_db(lambda db: gf_store.get_idea(db, r.json()["idea_id"]))
        self.assertEqual((row["wallet"], row["x_user_id"]), (a.address.lower(), "x1"))

    def test_service_errors_become_http_errors_with_stable_codes(self):
        a = self.link_x(1)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address, amount_wei=1)
        r = self.post_idea()
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "fee_tx_rejected"))
        bad = self.post_idea(name="A", ticker="$", lore="x", image=b"junk")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual({p["field"] for p in bad.json()["detail"]["problems"]}, {"name", "ticker", "lore", "image"})
        self.clock.now = T(13)
        closed = self.post_idea()
        self.assertEqual((closed.status_code, closed.json()["detail"]["code"]), (409, "submissions_closed"))

    def test_x_must_be_linked_first(self):
        a = acct(1)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        r = self.post_idea()
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (403, "x_not_linked"))

    def test_an_oversized_upload_is_refused_without_reading_it_all(self):
        a = self.link_x(1)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        huge = b"\x89PNG" + b"0" * (3 * 1024 * 1024)
        r = self.post_idea(image=huge)
        self.assertEqual((r.status_code, r.json()["detail"]["problems"][0]["code"]), (422, "too_large"))


class TestVoteEndpoint(ApiCase):
    def seed_pool(self):
        from test_gf_service import seed_pool
        self.run_db(lambda db: seed_pool(db, self.chain, 2))
        self.clock.now = T(15)

    def vote(self, voter, idea_id, signed_at=None, **kw):
        t = signed_at or int(self.clock.now.timestamp())
        return self.client.post("/api/goforge/vote", json={"voter": voter.address, "idea_id": idea_id, "round": kw.get("round", "2026-10-06"),
                                                          "signed_at": t, "signature": kw.get("signature") or sign_vote(voter, idea_id, t)})

    def test_a_signed_vote_counts_and_is_broadcast(self):
        self.seed_pool()
        v = acct(11)
        self.chain.balances[v.address.lower()] = 5000.0
        self.chain.first[v.address.lower()] = T(15) - timedelta(days=30)
        r = self.vote(v, "idea-1")
        self.assertEqual((r.status_code, r.json()["votes"]), (200, {"idea-1": 1}))
        self.assertEqual(self.broadcasts[-1], {"gf_vote": {"round_date": "2026-10-06", "votes": {"idea-1": 1}}})
        api.invalidate_cache()
        self.assertEqual(self.client.get("/api/goforge/round").json()["ideas"][0]["votes"], 1)

    def test_no_session_is_needed_but_the_signature_is(self):
        self.seed_pool()
        v = acct(11)
        self.chain.balances[v.address.lower()] = 5000.0
        self.chain.first[v.address.lower()] = T(15) - timedelta(days=30)
        forged = self.vote(v, "idea-1", signature="0x" + "12" * 65)
        self.assertEqual((forged.status_code, forged.json()["detail"]["code"]), (401, "bad_signature"))
        self.assertEqual(self.broadcasts, [])

    def test_ineligible_and_closed_votes_are_refused_with_codes(self):
        self.seed_pool()
        poor = acct(12)
        self.chain.balances[poor.address.lower()] = 1.0
        self.chain.first[poor.address.lower()] = T(15) - timedelta(days=30)
        r = self.vote(poor, "idea-1")
        self.assertEqual((r.status_code, r.json()["detail"]["code"], r.json()["detail"]["reason"]), (403, "not_eligible", "balance_too_low"))
        self.clock.now = T(21)
        late = self.vote(poor, "idea-1")
        self.assertEqual((late.status_code, late.json()["detail"]["code"]), (409, "voting_closed"))

    def test_typed_data_endpoint_gives_exactly_what_the_server_verifies(self):
        self.seed_pool()
        v = acct(11)
        self.chain.balances[v.address.lower()] = 5000.0
        self.chain.first[v.address.lower()] = T(15) - timedelta(days=30)
        typed = self.client.get("/api/goforge/vote/typed-data", params={"voter": v.address, "idea_id": "idea-2",
                                                                      "signed_at": int(self.clock.now.timestamp())}).json()
        sig = Account.sign_message(encode_typed_data(full_message=typed), v.key).signature.hex()
        r = self.client.post("/api/goforge/vote", json={"voter": v.address, "idea_id": "idea-2", "round": typed["message"]["round"],
                                                        "signed_at": typed["message"]["signedAt"], "signature": sig})
        self.assertEqual(r.status_code, 200)


class TestTransparency(ApiCase):
    def finish_round(self):
        async def seed(db):
            from test_gf_service import seed_pool, add_votes
            await seed_pool(db, self.chain, 3)
            await add_votes(db, self.chain, "idea-1", 100, 3)
            await add_votes(db, self.chain, "idea-2", 200, 1)
        self.run_db(seed)

    def close(self, announce=True):
        async def go(db):
            await svc.score_round(db, self.deps, D, T(20))
            if announce:
                await svc.announce_round(db, self.deps, D, T(20, 5))
        self.run_db(go)

    def test_scoreboard_and_votes_are_not_published_before_the_vote_closes(self):
        self.finish_round()
        for hour in (9, 15, 19):
            self.clock.now = T(hour)
            for path in ("scoreboard", "votes"):
                r = self.client.get(f"/api/goforge/{path}/2026-10-06")
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (404, "not_published"), (hour, path))

    def test_scoreboard_is_published_at_20_00_with_every_score_but_no_winner_mark_until_20_05(self):
        self.finish_round()
        self.close(announce=False)
        self.clock.now = T(20, 1)
        body = self.client.get("/api/goforge/scoreboard/2026-10-06").json()
        self.assertEqual([r["rank"] for r in body["rows"]], [1, 2, 3])
        self.assertEqual(set(body["rows"][0]) - {"image_url"}, {"rank", "idea_id", "name", "ticker", "x_handle", "credibility", "golem", "vote", "final", "votes", "winner"})
        self.assertFalse(any(r["winner"] for r in body["rows"]))
        self.assertEqual(len(body["scoreboard_sha256"]), 64)
        self.assertIn("0.40 x credibility", body["formula"])
        self.run_db(lambda db: svc.announce_round(db, self.deps, D, T(20, 5)))
        self.clock.now = T(20, 6)
        body = self.client.get("/api/goforge/scoreboard/2026-10-06").json()
        self.assertEqual([r["winner"] for r in body["rows"]], [True, False, False])
        self.assertNotIn('"wallet"', json.dumps(body))

    def test_the_published_table_reproduces_the_stored_hash(self):
        self.finish_round()
        self.close()
        self.clock.now = T(21)
        body = self.client.get("/api/goforge/scoreboard/2026-10-06").json()
        from app.services.gf_launch import scoreboard_hash
        self.assertEqual(scoreboard_hash(body["rows"]), body["scoreboard_sha256"])
        for r in body["rows"]:
            self.assertEqual(r["final"], round(0.4 * r["credibility"] + 0.3 * r["golem"] + 0.3 * r["vote"], 2))

    def test_the_vote_list_is_public_with_signatures_and_both_balances(self):
        self.finish_round()
        self.close()
        self.clock.now = T(21)
        body = self.client.get("/api/goforge/votes/2026-10-06").json()
        self.assertEqual(len(body["votes"]), 4)
        v = body["votes"][0]
        self.assertEqual(set(v), {"voter", "idea_id", "signature", "signed_at", "epc_balance_at_vote", "epc_balance_at_close", "counted", "voted_at"})
        self.assertTrue(all(x["counted"] for x in body["votes"]))
        self.assertEqual(body["typed_data_domain"]["name"], "EpochLabs GoForge")

    def test_bad_dates(self):
        self.assertEqual(self.client.get("/api/goforge/scoreboard/not-a-date").status_code, 422)
        self.assertEqual(self.client.get("/api/goforge/scoreboard/2020-01-01").status_code, 404)


class TestAdmin(ApiCase):
    H = {"x-admin-token": ADMIN, "x-admin-name": "alice"}

    def test_admin_is_off_without_a_token_and_closed_to_a_wrong_one(self):
        self.assertEqual(self.client.get("/api/goforge/admin/review").status_code, 403)
        self.assertEqual(self.client.get("/api/goforge/admin/review", headers={"x-admin-token": "nope"}).status_code, 403)
        with mock.patch.object(settings, "GF_ADMIN_TOKEN", ""):
            self.assertEqual(self.client.get("/api/goforge/admin/review", headers={"x-admin-token": ""}).status_code, 403)
        self.assertEqual(self.client.get("/api/goforge/admin/review", headers=self.H).status_code, 200)

    def submit(self, n=1, **kw):
        a = self.link_x(n)
        c = TestClient(self.app, follow_redirects=False)
        self.login(a, c)
        tx = "0x" + f"{n:02x}" * 32
        self.chain.add_fee_tx(tx, a.address)
        r = self.post_idea(client=c, fee_tx=tx, **kw)
        return r.json()["idea_id"]

    def test_the_review_queue_shows_the_flags_and_a_decision_moves_the_idea(self):
        i1 = self.submit(1)
        i2 = self.submit(2, name="Moss Keeper", ticker="MOSS", lore=LORE2)
        q = self.client.get("/api/goforge/admin/review", headers=self.H).json()["pending"]
        self.assertEqual([p["idea_id"] for p in q], [i1, i2])
        self.assertEqual(q[0]["flags"]["needs_visual_check"], True)
        self.assertEqual(len(q[0]["image_sha256"]), 64)
        ok = self.client.post(f"/api/goforge/admin/ideas/{i1}/review", headers=self.H, json={"status": "approved"})
        self.assertEqual(ok.status_code, 200)
        rej = self.client.post(f"/api/goforge/admin/ideas/{i2}/review", headers=self.H, json={"status": "rejected", "reason": "Image shows a brand logo"})
        self.assertEqual(rej.status_code, 200)
        self.assertEqual(self.client.get("/api/goforge/admin/review", headers=self.H).json()["pending"], [])
        row = self.run_db(lambda db: gf_store.get_idea(db, i2))
        self.assertEqual((row["status"], row["reject_reason"], row["reviewed_by"]), ("rejected", "Image shows a brand logo", "alice"))

    def test_review_rules(self):
        i = self.submit(1)
        bad = self.client.post(f"/api/goforge/admin/ideas/{i}/review", headers=self.H, json={"status": "maybe"})
        self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "bad_status"))
        no_reason = self.client.post(f"/api/goforge/admin/ideas/{i}/review", headers=self.H, json={"status": "rejected", "reason": "  "})
        self.assertEqual((no_reason.status_code, no_reason.json()["detail"]["code"]), (422, "reason_required"))
        unknown = self.client.post("/api/goforge/admin/ideas/nope/review", headers=self.H, json={"status": "approved"})
        self.assertEqual((unknown.status_code, unknown.json()["detail"]["code"]), (409, "not_reviewable"))
        self.assertEqual(self.client.post(f"/api/goforge/admin/ideas/{i}/review", json={"status": "approved"}).status_code, 403)

    def test_registering_a_launch_excludes_it_from_trading_and_broadcasts(self):
        async def seed(db):
            from test_gf_service import seed_pool, add_votes
            await seed_pool(db, self.chain, 2)
            await add_votes(db, self.chain, "idea-1", 100, 2)
            await svc.score_round(db, self.deps, D, T(20))
            await svc.announce_round(db, self.deps, D, T(20, 5))
        self.run_db(seed)
        ca, sp = "0x" + "ca" * 20, "0x" + "5b" * 20
        self.chain.codes = {ca: True, sp: True}
        body = {"idea_id": "idea-1", "ca": ca, "launch_tx": "0x" + "aa" * 32, "splitter_address": sp}
        self.assertEqual(self.client.post("/api/goforge/admin/launch", json=body).status_code, 403)
        r = self.client.post("/api/goforge/admin/launch", headers=self.H, json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ca"], ca)
        self.assertIn(ca, settings.desk_excluded_tokens)
        self.assertEqual(self.broadcasts[-1], {"gf_launch": {"idea_id": "idea-1", "ca": ca}})
        again = self.client.post("/api/goforge/admin/launch", headers=self.H, json=body)
        self.assertEqual((again.status_code, again.json()["detail"]["code"]), (409, "already_registered"))
        no_slot = self.client.post("/api/goforge/admin/launch", headers=self.H, json={**body, "idea_id": "idea-2", "ca": "0x" + "cb" * 20})
        self.assertEqual((no_slot.status_code, no_slot.json()["detail"]["code"]), (404, "no_launch_slot"))

    def test_wallet_change_is_logged_and_validated(self):
        self.link_x(1)
        new = acct(9).address
        ok = self.client.post("/api/goforge/admin/wallet-change", headers=self.H,
                              json={"x_user_id": "x1", "new_wallet": new, "reason": "Lost the key, verified by DM and a signed X post"})
        self.assertEqual(ok.status_code, 200)
        rows = asyncio.run(self._log())
        self.assertEqual(rows[0][1], new.lower())
        self.assertEqual(rows[0][3], "alice")
        for body in ({"x_user_id": "x1", "new_wallet": "nope", "reason": "long enough reason here"},
                     {"x_user_id": "x1", "new_wallet": new, "reason": "short"}):
            self.assertEqual(self.client.post("/api/goforge/admin/wallet-change", headers=self.H, json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/goforge/admin/wallet-change", headers=self.H,
                                          json={"x_user_id": "ghost", "new_wallet": new, "reason": "long enough reason here"}).status_code, 404)
        self.link_x(2)
        taken = self.client.post("/api/goforge/admin/wallet-change", headers=self.H,
                                 json={"x_user_id": "x2", "new_wallet": new, "reason": "long enough reason here"})
        self.assertEqual(taken.status_code, 409)

    async def _log(self):
        async with gf_pg.session() as db:
            return [tuple(r) for r in (await db.execute(text("SELECT old_wallet, new_wallet, reason, changed_by FROM gf_wallet_changes"))).all()]


class TestImages(ApiCase):
    def test_a_submitted_image_is_served_by_its_hash_and_nothing_else_is(self):
        a = self.link_x(1)
        self.login(a)
        self.chain.add_fee_tx("0x" + "01" * 32, a.address)
        idea = self.post_idea().json()
        r = self.client.get(idea["image_url"])
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/png", png()))
        self.assertIn("immutable", r.headers["cache-control"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        for bad in ("../secret.png", "..%2Fsecret.png", "x.png", ("a" * 64) + ".gif", ("A" * 64) + ".png", ("a" * 64) + ".png"):
            self.assertEqual(self.client.get(f"/api/goforge/images/{bad}").status_code, 404, bad)


class TestLaunchesArchive(ApiCase):
    def test_archive_merges_live_data_with_the_creator_and_the_fee_split_and_hides_prepool_launches(self):
        ca, sp = "0x" + "ca" * 20, "0x" + "5b" * 20

        async def seed(db):
            from test_gf_service import seed_pool, add_votes
            await seed_pool(db, self.chain, 2)
            await add_votes(db, self.chain, "idea-1", 100, 2)
            await svc.score_round(db, self.deps, D, T(20))
            await svc.announce_round(db, self.deps, D, T(20, 5))
            self.chain.codes = {ca: True, sp: True}
            await svc.register_launch(db, self.deps, idea_id="idea-1", ca=ca, launch_tx="0x" + "aa" * 32, splitter=sp, registered_by="t", now=T(21))
            await gf_store.insert_distribution(db, dict(tx_hash="0x" + "d1" * 32, log_index=0, idea_id="idea-1", block=1, at=T(22),
                                                        creator_wei=10 ** 18, burn_wei=10 ** 18, epc_burned_wei=4000 * 10 ** 18))
            await gf_store.refresh_launch_totals(db, "idea-1")
            await db.commit()
        self.run_db(seed)
        # before the pool is active nothing about the launch is public
        self.assertEqual(self.client.get("/api/goforge/launches").json()["launches"], [])
        async def go_live(db):
            await db.execute(text("UPDATE goforge_launches SET name = 'Idea 1 Keeper', symbol = 'TK1', launched_at = :t, pool_active_at = :t, "
                                  "last_source_ok_at = :t, peak_mc_usd = 12000 WHERE id = 'idea-1'"), {"t": T(21)})
            await db.execute(text("INSERT INTO goforge_snapshots (launch_id, ts, price_usd, mc_usd, liquidity_usd, volume_24h_usd, holders) "
                                  "VALUES ('idea-1', :t, 0.0001, 12000, 8000, 900, 55)"), {"t": T(21)})
            await db.commit()
        self.run_db(go_live)
        body = self.client.get("/api/goforge/launches").json()
        card = body["launches"][0]
        self.assertEqual((card["community"], card["idea_id"], card["name"], card["symbol"], card["creator_handle"]), (True, "idea-1", "Idea 1 Keeper", "TK1", "creator1"))
        self.assertEqual((card["mc_usd"], card["holders"], card["verdict"]), (12000.0, 55, "pending"))
        self.assertEqual((card["fees_to_creator_eth"], card["epc_burned_from_fees"]), (1.0, 4000.0))
        self.assertEqual(card["distributions"][0]["tx_url"].split("/tx/")[1], "0x" + "d1" * 32)
        self.assertEqual(card["distributions"][0]["epc_burned"], 4000.0)
        self.assertEqual(card["splitter_address"], sp)
        self.assertEqual(body["totals"]["launches"], 1)
        self.assertEqual(body["totals"]["fees_paid_to_creators_eth"], 1.0)
        for forbidden in ('"wallet"', '"fee_tx"', '"launch_due_at"', '"x_user_id"', acct(1).address.lower()):
            self.assertNotIn(forbidden, json.dumps(body))

    def test_empty_archive(self):
        body = self.client.get("/api/goforge/launches").json()
        self.assertEqual((body["launches"], body["totals"]["launches"]), ([], 0))


if __name__ == "__main__":
    unittest.main()
