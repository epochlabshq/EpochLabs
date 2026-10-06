"""
GoForge Registry API (brief section 10). A thin HTTP layer: every rule lives in app.services.gf_*.

Public:  GET  /round, /scoreboard/{date}, /votes/{date}, /launches
Login:   GET  /auth/nonce, POST /auth/siwe, GET /auth/me, POST /auth/logout, GET /auth/x, GET /auth/x/callback
Actions: POST /ideas (session + X), GET /ideas/mine, POST /vote (EIP-712 signature, no session needed)
Admin:   header X-Admin-Token. GET /admin/review, POST /admin/ideas/{id}/review, POST /admin/launch, POST /admin/wallet-change
"""
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.goforge_endpoints import load_rows, serialize_rows
from app.api.websocket import manager
from app.core.config import settings
from app.db.database import get_db
from app.db.gf_schema import ensure_gf_schema
from app.services import gf_chain, gf_identity, gf_launch, gf_rounds, gf_service as svc, gf_store

router = APIRouter(prefix="/api/goforge")

SESSION_COOKIE = "gf_session"
ROUND_CACHE_TTL = 60.0
_round_cache: dict = {"ts": 0.0, "key": None, "body": None}
_schema_ready = False
_deps: Optional[svc.Deps] = None


def invalidate_cache() -> None:
    _round_cache["ts"] = 0.0


def get_deps() -> svc.Deps:
    """The real dependencies. Tests override this with a fake chain."""
    global _deps
    if _deps is None:
        image_dir = Path(settings.GF_IMAGE_DIR or os.path.join(settings.STORAGE_LOCAL_PATH, "goforge"))
        from app.services.twitter_service import twitter_service
        _deps = svc.Deps(chain=gf_chain.GfChain(), embed=svc.default_embed_async, image_dir=image_dir,
                         image_base_url=settings.GF_IMAGE_BASE_URL, poster=twitter_service)
    return _deps


async def get_session_db(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    global _schema_ready
    if not _schema_ready:  # the API works even where the round worker is disabled or has not started yet
        await ensure_gf_schema(db)
        _schema_ready = True
    return db


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fail(e: svc.ServiceError):
    raise HTTPException(status_code=e.status, detail=e.as_dict())


def session_wallet(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
    return gf_identity.read_session(settings.GF_SESSION_SECRET, token)


def require_wallet(request: Request) -> str:
    wallet = session_wallet(request)
    if not wallet:
        raise HTTPException(status_code=401, detail={"code": "login_required", "message": "Connect your wallet first."})
    return wallet


def require_admin(request: Request) -> str:
    """Admin calls carry X-Admin-Token. With no token configured the admin API is off (403), never open."""
    expected = settings.GF_ADMIN_TOKEN
    given = request.headers.get("x-admin-token", "")
    if not expected or not gf_identity.constant_time_equals(given, expected):
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Admin access required."})
    return request.headers.get("x-admin-name", "team")[:40]


def cookie_secure() -> bool:
    return settings.GF_FRONTEND_URL.startswith("https://")


# ---------------------------------------------------------------------------
# Idea images
# ---------------------------------------------------------------------------

IMAGE_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


@router.get("/images/{name}")
async def get_image(name: str):
    """An idea image, by its SHA-256 name. Only that exact shape is served, so no path can leave the image folder."""
    if not IMAGE_NAME.match(name):
        raise HTTPException(404, detail={"code": "not_found", "message": "Image not found."})
    path = get_deps().image_dir / name
    if not path.is_file():
        raise HTTPException(404, detail={"code": "not_found", "message": "Image not found."})
    return FileResponse(path, media_type=IMAGE_TYPES[name.rsplit(".", 1)[1]],
                        headers={"cache-control": "public, max-age=31536000, immutable", "x-content-type-options": "nosniff"})


# ---------------------------------------------------------------------------
# Public round state
# ---------------------------------------------------------------------------

def public_winner(idea: dict) -> dict:
    return {**svc.public_idea(idea), "scores": {
        "credibility": float(idea["score_credibility"] or 0), "golem": float(idea["score_golem"] or 0),
        "vote": float(idea["score_vote"] or 0), "final": float(idea["score_final"] or 0)}}


def schedule_view(sch: gf_rounds.Schedule) -> dict:
    return {"submit_opens_hour_utc": sch.open_hour, "submit_closes_hour_utc": sch.close_hour,
            "vote_closes_hour_utc": sch.vote_close_hour, "announce_minute_utc": sch.announce_minute,
            "launch_window_hours": sch.launch_window_hours}


def rules_view() -> dict:
    return {
        "max_ideas_per_day": settings.GF_MAX_IDEAS_PER_DAY, "submit_fee_epc": settings.GF_SUBMIT_FEE_EPC,
        "vote_min_epc": settings.GF_VOTE_MIN_EPC, "vote_min_wallet_age_days": settings.GF_VOTE_MIN_WALLET_AGE_DAYS,
        "vote_changes_per_hour": settings.GF_VOTE_CHANGES_PER_HOUR, "win_cooldown_days": settings.GF_WIN_COOLDOWN_DAYS,
        "weights": {"credibility": 0.40, "golem": 0.30, "vote": 0.30}, "chain_id": settings.CHAIN_ID,
        "epc_token": settings.EPOCH_TOKEN_CA, "burn_address": gf_chain.DEAD_ADDRESS,
        "similarity_threshold": settings.GF_SIMILARITY_THRESHOLD,
        "login_configured": bool(settings.GF_SESSION_SECRET),
        "x_login_configured": bool(settings.GF_X_CLIENT_ID and settings.GF_X_REDIRECT_URI),
    }


def build_round_payload(*, now: datetime, sch: gf_rounds.Schedule, rnd: dict, approved: list[dict], votes: dict[str, int],
                        n_submitted: int, winner: Optional[dict], last_round: Optional[dict], totals: dict) -> dict:
    """GET /round. The pool is hidden until submissions close, so nobody can copy an idea they can already see."""
    phase = gf_rounds.phase_at(now, sch)
    label, at = gf_rounds.next_boundary(now, sch)
    pool_visible = phase != "submit" or settings.GF_DEV_OPEN
    announced = rnd["status"] in ("announced", "launched")
    return {
        "round_date": rnd["round_date"].isoformat(), "phase": phase, "status": rnd["status"], "now": now.isoformat(),
        "next": {"label": label, "at": at.isoformat()},
        "slots": {"used": n_submitted, "max": settings.GF_MAX_IDEAS_PER_DAY},
        "pool_visible": pool_visible, "dev_open": settings.GF_DEV_OPEN,
        "ideas": [svc.public_idea(i, votes.get(i["idea_id"], 0)) for i in approved] if pool_visible else [],
        "n_ideas_approved": len(approved) if pool_visible else None,
        "n_votes": sum(votes.values()) if pool_visible else None,
        "winner": public_winner(winner) if (winner and announced) else None,
        "no_launch_reason": rnd.get("no_launch_reason") if rnd["status"] in ("no_launch",) or (phase == "announced" and not rnd.get("winner_idea_id") and rnd.get("no_launch_reason")) else None,
        "scoreboard_available": gf_rounds.scoreboard_visible(now, rnd["round_date"], sch) and rnd["status"] != "submit" and rnd["status"] != "vote",
        "last_round": last_round, "totals": totals, "rules": rules_view(), "schedule": schedule_view(sch),
    }


async def last_round_summary(db, current) -> Optional[dict]:
    for r in await gf_store.recent_rounds(db, 4):
        if r["round_date"] < current and r["status"] in ("announced", "launched", "no_launch"):
            win = await gf_store.get_idea(db, r["winner_idea_id"]) if r["winner_idea_id"] else None
            creator = await gf_store.creator_by_x(db, win["x_user_id"]) if win else None
            return {"round_date": r["round_date"].isoformat(), "status": r["status"], "no_launch_reason": r["no_launch_reason"],
                    "winner": ({**svc.public_idea({**win, "x_handle": (creator or {}).get("x_handle")})} if win else None),
                    "n_ideas": r["n_ideas"], "n_votes": r["n_votes"]}
    return None


@router.get("/round")
async def get_round(db: AsyncSession = Depends(get_session_db)):
    """Today's round: phase, countdown, slots, the pool (from the vote on), the winner (from the announcement)."""
    deps = get_deps()
    now = now_utc()
    sch = deps.schedule
    rd = gf_rounds.round_date_at(now, sch)
    key = (rd, gf_rounds.phase_at(now, sch))
    if _round_cache["body"] is not None and _round_cache["key"] == key and time.time() - _round_cache["ts"] < ROUND_CACHE_TTL:
        return _round_cache["body"]
    rnd = await gf_store.ensure_round(db, rd)
    approved = await gf_store.ideas_of_round(db, rd, ["approved"])
    votes = await gf_store.vote_counts(db, rd)
    winner = next((i for i in await gf_store.ideas_of_round(db, rd, ["approved"]) if i["idea_id"] == rnd["winner_idea_id"]), None)
    if winner:
        winner = {**winner, "x_handle": winner.get("x_handle")}
    body = build_round_payload(
        now=now, sch=sch, rnd=rnd, approved=approved, votes=votes, n_submitted=await gf_store.count_ideas(db, rd), winner=winner,
        last_round=await last_round_summary(db, rd), totals=await gf_store.totals(db))
    await db.commit()
    _round_cache.update(ts=time.time(), key=key, body=body)
    return body


# ---------------------------------------------------------------------------
# Login: SIWE, session, X
# ---------------------------------------------------------------------------

class SiweBody(BaseModel):
    message: str
    signature: str


@router.get("/auth/nonce")
async def auth_nonce(address: Optional[str] = None, db: AsyncSession = Depends(get_session_db)):
    """
    A one-time nonce and the fields the wallet needs to build the EIP-4361 message. EIP-4361 wants the address in its
    EIP-55 (checksum) form and wallets refuse a message whose address differs from the signing account, but they hand the
    page a lower-case address: pass it as `address` and the checksummed one comes back to put in the message.
    """
    if not settings.GF_SESSION_SECRET:
        raise HTTPException(503, detail={"code": "login_not_configured", "message": "Login is not configured yet."})
    nonce = gf_identity.new_nonce()
    await gf_store.store_nonce(db, nonce)
    await db.commit()
    checksummed = None
    if address is not None:
        if not gf_identity.ADDRESS_RE.match(address):
            raise HTTPException(422, detail={"code": "bad_address", "message": "That is not a valid wallet address."})
        checksummed = gf_identity.to_checksum_address(address)
    return {"address": checksummed, "nonce": nonce, "domain": settings.GF_SIWE_DOMAIN, "uri": settings.GF_SIWE_URI, "chain_id": settings.CHAIN_ID,
            "statement": gf_identity.SIWE_STATEMENT, "issued_at": now_utc().strftime("%Y-%m-%dT%H:%M:%S.000Z")}


@router.post("/auth/siwe")
async def auth_siwe(body: SiweBody, response: Response, db: AsyncSession = Depends(get_session_db)):
    """Verify the signature (the wallet is recovered from it), burn the nonce, open a session."""
    if not settings.GF_SESSION_SECRET:
        raise HTTPException(503, detail={"code": "login_not_configured", "message": "Login is not configured yet."})
    try:
        info = gf_identity.verify_siwe(body.message, body.signature, expected_domain=settings.GF_SIWE_DOMAIN,
                                       expected_chain_id=settings.CHAIN_ID, now=now_utc())
    except gf_identity.IdentityError as e:
        raise HTTPException(401, detail={"code": "bad_signature", "message": str(e)})
    if not await gf_store.consume_nonce(db, info["nonce"]):
        raise HTTPException(401, detail={"code": "bad_nonce", "message": "That sign-in link was already used or has expired."})
    await db.commit()
    token = gf_identity.issue_session(settings.GF_SESSION_SECRET, info["address"], now_utc(), settings.GF_SESSION_TTL_HOURS)
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.GF_SESSION_TTL_HOURS * 3600, httponly=True,
                        samesite="lax", secure=cookie_secure(), path="/")
    return await me_payload(db, info["address"])


@router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


async def me_payload(db, wallet: str) -> dict:
    creator = await gf_store.creator_by_wallet(db, wallet)
    now = now_utc()
    rd = gf_rounds.round_date_at(now, get_deps().schedule)
    vote = await gf_store.get_vote(db, rd, wallet)
    return {
        "wallet": wallet,
        "x": ({"handle": creator["x_handle"], "verified": creator["x_verified"], "followers": creator["x_followers"]}
              if creator else None),
        "is_team": gf_identity.is_team_identity(wallet, creator["x_user_id"] if creator else None, settings.gf_team_wallets,
                                                settings.gf_team_x_user_ids),
        "my_vote": {"round_date": rd.isoformat(), "idea_id": vote["idea_id"]} if vote else None,
        "submitted_today": await gf_store.wallet_idea_today(db, wallet, rd),
    }


@router.get("/auth/me")
async def auth_me(request: Request, db: AsyncSession = Depends(get_session_db)):
    wallet = session_wallet(request)
    if not wallet:
        return {"wallet": None, "x": None, "is_team": False, "my_vote": None, "submitted_today": False}
    return await me_payload(db, wallet)


def frontend_redirect(**params) -> RedirectResponse:
    from urllib.parse import urlencode
    base = settings.GF_FRONTEND_URL
    return RedirectResponse(f"{base}{'&' if '?' in base else '?'}{urlencode(params)}", status_code=302)


@router.get("/auth/x")
async def auth_x_start(request: Request, db: AsyncSession = Depends(get_session_db)):
    """Start the X OAuth 2.0 (PKCE) login for the wallet that is signed in."""
    wallet = require_wallet(request)
    if not (settings.GF_X_CLIENT_ID and settings.GF_X_REDIRECT_URI):
        raise HTTPException(503, detail={"code": "x_not_configured", "message": "X login is not configured yet."})
    verifier, challenge = gf_identity.pkce_pair()
    state = gf_identity.new_nonce()
    await gf_store.store_oauth_state(db, state, wallet, verifier)
    await db.commit()
    return RedirectResponse(gf_identity.x_authorize_url(settings.GF_X_CLIENT_ID, settings.GF_X_REDIRECT_URI, state, challenge), status_code=302)


async def x_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


@router.get("/auth/x/callback")
async def auth_x_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None,
                          db: AsyncSession = Depends(get_session_db)):
    """X sends the user back here. The wallet comes from the stored state, the account from the X API for this token."""
    if error or not code or not state:
        return frontend_redirect(x_error="cancelled")
    pending = await gf_store.pop_oauth_state(db, state)
    await db.commit()
    if not pending:
        return frontend_redirect(x_error="expired")
    # the same browser must still hold the session that started the flow
    if session_wallet(request) != pending["wallet"]:
        return frontend_redirect(x_error="session_mismatch")
    try:
        async with await x_client() as client:
            token = await gf_identity.x_exchange_code(client, client_id=settings.GF_X_CLIENT_ID, client_secret=settings.GF_X_CLIENT_SECRET,
                                                      redirect_uri=settings.GF_X_REDIRECT_URI, code=code, verifier=pending["code_verifier"])
            user = await gf_identity.x_fetch_user(client, token)
    except gf_identity.IdentityError:
        return frontend_redirect(x_error="x_rejected")
    if gf_identity.is_team_identity(pending["wallet"], user["x_user_id"], settings.gf_team_wallets, settings.gf_team_x_user_ids):
        return frontend_redirect(x_error="team")
    action, _reason = gf_identity.link_decision(await gf_store.creator_by_x(db, user["x_user_id"]),
                                                await gf_store.creator_by_wallet(db, pending["wallet"]),
                                                user["x_user_id"], pending["wallet"])
    if action == "refuse":
        return frontend_redirect(x_error="already_linked")
    await gf_store.upsert_creator(db, user, pending["wallet"])
    await db.commit()
    return frontend_redirect(x="linked")


# ---------------------------------------------------------------------------
# Ideas and votes
# ---------------------------------------------------------------------------

@router.post("/ideas")
async def post_idea(request: Request, name: str = Form(...), ticker: str = Form(...), lore: str = Form(...),
                    fee_tx: str = Form(...), image: UploadFile = File(...), db: AsyncSession = Depends(get_session_db)):
    """Submit an idea. Wallet and X account come from the session, never from the form."""
    wallet = require_wallet(request)
    data = await image.read(gf_validation_max() + 1)
    try:
        out = await svc.submit_idea(db, get_deps(), wallet=wallet, name=name, ticker=ticker, lore=lore, image=data, fee_tx=fee_tx, now=now_utc())
    except svc.ServiceError as e:
        await db.rollback()
        fail(e)
    invalidate_cache()
    return out


def gf_validation_max() -> int:
    from app.services.gf_validation import IMAGE_MAX_BYTES
    return IMAGE_MAX_BYTES


@router.get("/ideas/mine")
async def ideas_mine(request: Request, db: AsyncSession = Depends(get_session_db)):
    wallet = require_wallet(request)
    return {"ideas": [svc.own_idea(i) for i in await gf_store.ideas_of_wallet(db, wallet)]}


class VoteBody(BaseModel):
    voter: str
    idea_id: str
    round: str
    signed_at: int
    signature: str


@router.post("/vote")
async def post_vote(body: VoteBody, db: AsyncSession = Depends(get_session_db)):
    """A gasless vote: the EIP-712 signature identifies the wallet, so no session is needed."""
    try:
        out = await svc.cast_vote(db, get_deps(), wallet=body.voter, idea_id=body.idea_id, round_date=body.round,
                                  signed_at=body.signed_at, signature=body.signature, now=now_utc())
    except svc.ServiceError as e:
        await db.rollback()
        fail(e)
    invalidate_cache()
    await manager.broadcast({"gf_vote": {"round_date": body.round, "votes": out["votes"]}})
    return out


@router.get("/vote/typed-data")
async def vote_typed_data(voter: str, idea_id: str, signed_at: Optional[int] = None):
    """The exact EIP-712 message to sign, so the browser and the server can never disagree on its shape."""
    now = now_utc()
    rd = gf_rounds.round_date_at(now, get_deps().schedule).isoformat()
    if not gf_identity.ADDRESS_RE.match(voter):
        raise HTTPException(422, detail={"code": "bad_address", "message": "That is not a valid wallet address."})
    return gf_identity.vote_typed_data(round_date=rd, idea_id=idea_id, voter=gf_identity.to_checksum_address(voter),
                                       signed_at=signed_at or int(now.timestamp()),
                                       chain_id=settings.CHAIN_ID)


# ---------------------------------------------------------------------------
# Transparency: scoreboard and votes
# ---------------------------------------------------------------------------

def parse_date(value: str):
    from datetime import date
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, detail={"code": "bad_date", "message": "Use YYYY-MM-DD."})


@router.get("/scoreboard/{round_date}")
async def get_scoreboard(round_date: str, db: AsyncSession = Depends(get_session_db)):
    """The full table of the day, from the moment the vote closes. Never earlier."""
    d = parse_date(round_date)
    sch = get_deps().schedule
    rnd = await gf_store.get_round(db, d)
    if rnd is None or not gf_rounds.scoreboard_visible(now_utc(), d, sch) or rnd["status"] in ("submit", "vote", "review"):
        raise HTTPException(404, detail={"code": "not_published", "message": "The scoreboard is published when the vote closes."})
    ideas = await gf_store.ideas_of_round(db, d, ["approved"])
    from app.services.gf_scoring import ScoredIdea
    scored = [ScoredIdea(i["idea_id"], i["submitted_at"], float(i["score_credibility"] or 0), float(i["score_golem"] or 0),
                         float(i["score_vote"] or 0), float(i["score_final"] or 0), int(i["votes_counted"] or 0), i["x_user_id"], i["wallet"])
              for i in ideas]
    announced = rnd["status"] in ("announced", "launched")
    rows = svc.scoreboard_rows(ideas, scored, rnd["winner_idea_id"], reveal_winner=announced)
    for r in rows:
        r.pop("detail", None)
    return {"round_date": d.isoformat(), "status": rnd["status"], "rows": rows, "scoreboard_sha256": rnd["scoreboard_sha256"],
            "formula": "final = 0.40 x credibility + 0.30 x golem + 0.30 x vote",
            "n_ideas": rnd["n_ideas"], "n_votes": rnd["n_votes"], "no_launch_reason": rnd["no_launch_reason"],
            "announced_at": rnd["announced_at"].isoformat() if rnd["announced_at"] else None}


@router.get("/votes/{round_date}")
async def get_votes(round_date: str, db: AsyncSession = Depends(get_session_db)):
    """Every vote with its signature, from the moment the vote closes, so the result can be audited."""
    d = parse_date(round_date)
    rnd = await gf_store.get_round(db, d)
    if rnd is None or not gf_rounds.votes_visible(now_utc(), d, get_deps().schedule) or rnd["status"] in ("submit", "vote", "review"):
        raise HTTPException(404, detail={"code": "not_published", "message": "Votes are published when the vote closes."})
    votes = await gf_store.votes_of_round(db, d)
    return {"round_date": d.isoformat(), "min_epc": settings.GF_VOTE_MIN_EPC, "votes": [{
        "voter": v["voter_wallet"], "idea_id": v["idea_id"], "signature": v["signature"], "signed_at": v["signed_at"],
        "epc_balance_at_vote": float(v["epc_balance_vote"]) if v["epc_balance_vote"] is not None else None,
        "epc_balance_at_close": float(v["epc_balance_close"]) if v["epc_balance_close"] is not None else None,
        "counted": v["counted"], "voted_at": v["voted_at"].isoformat() if v["voted_at"] else None} for v in votes],
        "typed_data_domain": gf_identity.vote_typed_data(round_date=d.isoformat(), idea_id="", voter="0x" + "0" * 40, signed_at=0,
                                                         chain_id=settings.CHAIN_ID)["domain"]}


# ---------------------------------------------------------------------------
# Forged tokens (archive)
# ---------------------------------------------------------------------------

def merge_forged(card: dict, launch: Optional[dict], distributions: list[dict]) -> dict:
    """Add the registry facts (idea, creator, fee split) to a launch card built by the live-data serializer."""
    if not launch:
        return {**card, "community": False}
    blockscout = settings.BLOCKSCOUT_BASE.rstrip("/")
    return {
        **card, "community": True, "idea_id": launch["idea_id"], "name": launch["name"], "symbol": launch["ticker"],
        "image_url": launch["image_url"], "creator_handle": launch.get("x_handle"), "round_date": launch["round_date"].isoformat(),
        "splitter_address": launch["splitter_address"],
        "fees_to_creator_eth": float(launch["fees_to_creator"] or 0), "epc_burned_from_fees": float(launch["epc_burned"] or 0),
        "distributions": [{"tx_hash": d["tx_hash"], "tx_url": f"{blockscout}/tx/{d['tx_hash']}", "at": d["at"].isoformat(),
                           "creator_eth": float(d["creator_wei"]) / 1e18, "burn_eth": float(d["burn_wei"]) / 1e18,
                           "epc_burned": float(d["epc_burned_wei"]) / 10 ** settings.GF_EPC_DECIMALS} for d in distributions],
        "scoreboard_url": f"{settings.GOFORGE_PUBLIC_URL}?round={launch['round_date'].isoformat()}",
    }


@router.get("/launches")
async def get_launches(db: AsyncSession = Depends(get_session_db)):
    """The archive of forged tokens, newest first, with live data (market cap, 48h verdict) and the fee split."""
    now = now_utc()
    rows, latest = await load_rows(db)
    cards = serialize_rows(rows, latest, now)
    gf = {l["idea_id"]: l for l in await gf_store.launches_with_ca(db)}
    out = []
    for c in cards:
        l = gf.get(c["id"])
        out.append(merge_forged(c, l, await gf_store.distributions_of(db, c["id"]) if l else []))
    return {"launches": out, "totals": await gf_store.totals(db), "generated_at": now.isoformat()}


# ---------------------------------------------------------------------------
# Admin (manual review, launch registration, wallet change)
# ---------------------------------------------------------------------------

class ReviewBody(BaseModel):
    status: str
    reason: Optional[str] = None


class LaunchBody(BaseModel):
    idea_id: str
    ca: str
    launch_tx: str
    splitter_address: Optional[str] = None


class WalletChangeBody(BaseModel):
    x_user_id: str
    new_wallet: str
    reason: str


@router.get("/admin/review")
async def admin_review_queue(admin: str = Depends(require_admin), db: AsyncSession = Depends(get_session_db)):
    """Ideas waiting for a person. Includes the automatic flags (the image still needs a visual check)."""
    rows = await gf_store.pending_review(db)
    return {"pending": [{**svc.public_idea(r), "round_date": r["round_date"].isoformat(), "wallet": r["wallet"],
                         "image_sha256": r["image_sha256"], "flags": r["auto_flags"]} for r in rows]}


@router.post("/admin/ideas/{idea_id}/review")
async def admin_review(idea_id: str, body: ReviewBody, admin: str = Depends(require_admin), db: AsyncSession = Depends(get_session_db)):
    if body.status not in ("approved", "rejected"):
        raise HTTPException(422, detail={"code": "bad_status", "message": "status must be approved or rejected."})
    if body.status == "rejected" and not (body.reason or "").strip():
        raise HTTPException(422, detail={"code": "reason_required", "message": "A rejection needs a short reason the submitter can read."})
    ok = await gf_store.review_idea(db, idea_id, body.status, (body.reason or "").strip()[:300] or None, admin)
    await db.commit()
    if not ok:
        raise HTTPException(409, detail={"code": "not_reviewable", "message": "Unknown idea, or it already won a round."})
    invalidate_cache()
    return {"idea_id": idea_id, "status": body.status}


@router.post("/admin/launch")
async def admin_register_launch(body: LaunchBody, admin: str = Depends(require_admin), db: AsyncSession = Depends(get_session_db)):
    """Register the CA after the team launched the winner on Pons. The CA joins the trading exclude list at once."""
    try:
        out = await svc.register_launch(db, get_deps(), idea_id=body.idea_id, ca=body.ca, launch_tx=body.launch_tx,
                                        splitter=body.splitter_address, registered_by=admin, now=now_utc())
    except svc.ServiceError as e:
        await db.rollback()
        fail(e)
    invalidate_cache()
    await manager.broadcast({"gf_launch": {"idea_id": body.idea_id, "ca": out["ca"]}})
    return {k: v for k, v in out.items() if k != "entry"}


@router.post("/admin/wallet-change")
async def admin_wallet_change(body: WalletChangeBody, admin: str = Depends(require_admin), db: AsyncSession = Depends(get_session_db)):
    """The manual wallet replacement for an X account: always logged with a reason, never self-service."""
    if not gf_launch.ADDRESS_RE.match(body.new_wallet) or len(body.reason.strip()) < 10:
        raise HTTPException(422, detail={"code": "invalid", "message": "A valid wallet and a reason of at least 10 characters are required."})
    try:
        await gf_store.change_wallet(db, body.x_user_id, body.new_wallet, body.reason.strip(), admin)
        await db.commit()
    except LookupError:
        raise HTTPException(404, detail={"code": "no_creator", "message": "No such X account."})
    except Exception:
        await db.rollback()
        raise HTTPException(409, detail={"code": "wallet_taken", "message": "That wallet is linked to another X account."})
    return {"x_user_id": body.x_user_id, "wallet": body.new_wallet.lower()}
