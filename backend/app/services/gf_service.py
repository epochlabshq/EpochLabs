"""
GoForge Registry application services: submit, vote, review, close the round, announce, register a launch, copy
verdicts, index fee distributions and send the X posts. They orchestrate the pure rules (gf_validation, gf_scoring,
gf_rounds, gf_identity, gf_launch), the database (gf_store) and the chain (gf_chain). The HTTP layer and the worker are
thin callers, so each flow is tested once, against a real database, with a fake chain.

Every refusal is a ServiceError with a stable `code`, so the frontend and the tests never match on prose.
"""
import inspect
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import text

from app.core.config import settings
from app.core import goforge_config
from app.core.goforge_config import LaunchEntry
from app.services import gf_chain, gf_identity, gf_launch, gf_poster, gf_rounds, gf_scoring, gf_store, gf_validation


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


@dataclass
class Deps:
    """Everything with side effects outside the database, so tests can replace it."""
    chain: Any
    embed: Callable[[list[str]], tuple[list[list[float]], str]]
    image_dir: Path
    image_base_url: str = "/api/goforge/images"
    image_moderator: Optional[Callable[[bytes], list[str]]] = None
    schedule: gf_rounds.Schedule = field(default_factory=lambda: gf_rounds.Schedule.from_settings(settings))
    blocklists: Optional[gf_validation.BlockLists] = None
    poster: Any = None
    hour_rates: Callable[..., Any] = None


async def default_embed_async(texts: list[str]) -> tuple[list[list[float]], str]:
    """default_embed off the event loop: the first call loads the model, which takes seconds."""
    import asyncio
    return await asyncio.to_thread(default_embed, texts)


def default_embed(texts: list[str]) -> tuple[list[list[float]], str]:
    """The Radar's MiniLM when an embedder is installed, else the deterministic hashed fallback (and it says so)."""
    try:
        from app.services.radar_worker import _embed
        res = _embed(texts)
        if res is not None:
            return [list(map(float, v)) for v in res[0]], res[1]
    except Exception as e:
        print(f"[GF] embedder unavailable, using the hashed fallback: {type(e).__name__} {e}", flush=True)
    return [gf_validation.hash_embedding(t) for t in texts], "hashed 3-gram fallback"


def new_idea_id(round_date: date) -> str:
    return f"{round_date:%Y%m%d}-{uuid.uuid4().hex[:8]}"


async def _xact_lock(db, name: str) -> None:
    """Serialise a critical section across workers and requests (slot cap, one idea per wallet) until the commit."""
    key = int.from_bytes(__import__("hashlib").sha256(name.encode()).digest()[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def public_idea(row: dict, votes: Optional[int] = None) -> dict:
    """An idea as the world sees it: no wallet, no fee tx, no moderation flags."""
    return {
        "idea_id": row["idea_id"], "name": row["name"], "ticker": row["ticker"], "lore": row["lore"],
        "image_url": row["image_url"], "creator_handle": row.get("x_handle"), "votes": votes,
        "submitted_at": row["submitted_at"].isoformat() if row.get("submitted_at") else None,
    }


def own_idea(row: dict) -> dict:
    """What the submitter sees about their own idea: the status and, when rejected, the reason."""
    return {**public_idea(row), "round_date": row["round_date"].isoformat(), "status": row["status"],
            "reject_reason": row.get("reject_reason") if row["status"] == "rejected" else None}


async def submit_idea(db, deps: Deps, *, wallet: str, name: str, ticker: str, lore: str, image: bytes, fee_tx: str,
                      now: datetime) -> dict:
    sch = deps.schedule
    wallet = wallet.lower()
    if not (settings.GF_DEV_OPEN or gf_rounds.submit_open(now, sch)):
        raise ServiceError(409, "submissions_closed", "Submissions are closed. They open again at "
                           f"{sch.open_hour:02d}:00 UTC.")
    creator = await gf_store.creator_by_wallet(db, wallet)
    if creator is None:
        raise ServiceError(403, "x_not_linked", "Connect your X account before submitting.")
    if gf_identity.is_team_identity(wallet, creator["x_user_id"], settings.gf_team_wallets, settings.gf_team_x_user_ids):
        raise ServiceError(403, "team_not_allowed", "The Epoch Labs team cannot submit ideas.")

    fields, problems = gf_validation.validate_fields(name, ticker, lore)
    img_info, img_problems = gf_validation.validate_image(image)
    problems = list(problems) + list(img_problems)
    if problems:
        raise ServiceError(422, "invalid_idea", "Some fields need a fix.", {"problems": [p.as_dict() for p in problems]})

    free = settings.GF_SUBMIT_FEE_EPC <= 0   # GF_SUBMIT_FEE_EPC=0: no fee, no transaction to check
    if free:
        fee_tx = f"free:{uuid.uuid4().hex}"  # the column is UNIQUE NOT NULL: a placeholder that can never collide
    else:
        if not (fee_tx or "").startswith("0x") or len(fee_tx) != 66:
            raise ServiceError(422, "fee_tx_invalid", "The submit fee transaction hash is not valid.")
        fee_tx = fee_tx.lower()

    round_date = gf_rounds.round_date_at(now, sch)
    await gf_store.ensure_round(db, round_date)
    # cheap checks first, then the chain
    if await gf_store.wallet_idea_today(db, wallet, round_date):
        raise ServiceError(429, "already_submitted_today", "This wallet already submitted an idea today.")
    if await gf_store.count_ideas(db, round_date) >= settings.GF_MAX_IDEAS_PER_DAY:
        raise ServiceError(409, "slots_full", f"All {settings.GF_MAX_IDEAS_PER_DAY} slots for today are taken.")
    burned_wei = 0
    if not free:
        if await gf_store.fee_tx_used(db, fee_tx):
            raise ServiceError(409, "fee_tx_used", "That fee transaction was already used for another idea.")

        info = await deps.chain.fee_tx(fee_tx)
        if info is None:
            raise ServiceError(503, "chain_unavailable", "Could not read the fee transaction right now. Try again in a minute.")
        _tx, receipt, block_time = info
        need = gf_chain.epc_to_wei(settings.GF_SUBMIT_FEE_EPC, settings.GF_EPC_DECIMALS)
        check = gf_chain.check_fee_receipt(receipt, epc=settings.EPOCH_TOKEN_CA, burn=gf_chain.DEAD_ADDRESS, sender=wallet,
                                           min_amount_wei=need)
        if not check.ok:
            raise ServiceError(422, "fee_tx_rejected", FEE_MESSAGES.get(check.reason, "The fee transaction is not valid."),
                               {"reason": check.reason})
        if not gf_chain.fee_tx_fresh(block_time, now):
            raise ServiceError(422, "fee_tx_rejected", "The fee transaction is too old. Send a new one.", {"reason": "too_old"})
        burned_wei = check.amount_wei

    embedded = deps.embed([fields["lore"]])
    if inspect.isawaitable(embedded):
        embedded = await embedded
    vectors, embedder = embedded
    embedding = vectors[0]
    others = [gf_validation.OtherIdea(o["idea_id"], o["name"], o["ticker"], o.get("lore_embedding"))
              for o in await gf_store.same_day_others(db, round_date)]
    moderation = gf_validation.auto_moderate(
        name=fields["name"], ticker=fields["ticker"], embedding=embedding, others=others,
        existing_symbols=await gf_store.existing_tickers(db, fields["ticker"]),
        blocklists=deps.blocklists or gf_validation.get_blocklists(), threshold=settings.GF_SIMILARITY_THRESHOLD,
        image_bytes=image, image_moderator=deps.image_moderator, embedder_label=embedder)

    # Development (GF_DEV_OPEN): an idea that passed the automatic checks joins the pool at once, without the manual review
    status = "approved" if settings.GF_DEV_OPEN and moderation.status == "pending_review" else moderation.status

    deps.image_dir.mkdir(parents=True, exist_ok=True)
    path = deps.image_dir / f"{img_info.sha256}.{img_info.ext}"
    if not path.exists():
        path.write_bytes(image)
    idea = {
        "idea_id": new_idea_id(round_date), "round_date": round_date, "x_user_id": creator["x_user_id"], "wallet": wallet,
        "name": fields["name"], "ticker": fields["ticker"], "lore": fields["lore"],
        "image_url": f"{deps.image_base_url.rstrip('/')}/{img_info.sha256}.{img_info.ext}", "image_sha256": img_info.sha256,
        "fee_tx": fee_tx, "status": status, "reject_reason": moderation.reason,
        "auto_flags": {**moderation.flags, "fee_epc_burned": gf_chain.wei_to_epc(burned_wei, settings.GF_EPC_DECIMALS)},
        "lore_embedding": embedding, "submitted_at": now,
    }
    # The slot cap and the one-per-wallet rule are re-checked under a lock, so two simultaneous requests cannot both pass
    await _xact_lock(db, f"gf-submit-{round_date}")
    if await gf_store.count_ideas(db, round_date) >= settings.GF_MAX_IDEAS_PER_DAY:
        raise ServiceError(409, "slots_full", f"All {settings.GF_MAX_IDEAS_PER_DAY} slots for today are taken.")
    if await gf_store.wallet_idea_today(db, wallet, round_date):
        raise ServiceError(429, "already_submitted_today", "This wallet already submitted an idea today.")
    try:
        await gf_store.insert_idea(db, idea)
    except Exception as e:  # the UNIQUE fee_tx is the last line of defence against a raced reuse
        if "fee_tx" in str(e) or "unique" in str(e).lower():
            await db.rollback()
            raise ServiceError(409, "fee_tx_used", "That fee transaction was already used for another idea.")
        raise
    await db.commit()
    return own_idea({**idea, "x_handle": creator.get("x_handle")})


FEE_MESSAGES = {
    "tx_not_found": "The fee transaction was not found.",
    "tx_failed": "The fee transaction failed onchain.",
    "no_epc_burn_from_wallet": "That transaction did not send EPC from your wallet to the burn address.",
    "amount_too_low": "The fee transaction sent less EPC than the submit fee.",
}


# ---------------------------------------------------------------------------
# Vote
# ---------------------------------------------------------------------------

async def cast_vote(db, deps: Deps, *, wallet: str, idea_id: str, round_date: str, signed_at: int, signature: str,
                    now: datetime) -> dict:
    sch = deps.schedule
    wallet = wallet.lower()
    if not (settings.GF_DEV_OPEN or gf_rounds.vote_open(now, sch)):
        raise ServiceError(409, "voting_closed", "Voting is not open right now.")
    current = gf_rounds.round_date_at(now, sch)
    if round_date != current.isoformat():
        raise ServiceError(409, "wrong_round", "That vote is for a different round.")
    try:
        gf_identity.verify_vote(round_date=round_date, idea_id=idea_id, voter=wallet, signed_at=signed_at,
                                chain_id=settings.CHAIN_ID, signature=signature, now=now)
    except gf_identity.IdentityError as e:
        raise ServiceError(401, "bad_signature", str(e))

    voter = await gf_store.creator_by_wallet(db, wallet)
    voter_x = voter["x_user_id"] if voter else None
    if gf_identity.is_team_identity(wallet, voter_x, settings.gf_team_wallets, settings.gf_team_x_user_ids):
        raise ServiceError(403, "team_not_allowed", "The Epoch Labs team cannot vote.")
    idea = await gf_store.get_idea(db, idea_id)
    if idea is None or idea["round_date"] != current or idea["status"] != "approved":
        raise ServiceError(404, "idea_not_votable", "That idea is not in today's pool.")
    if gf_scoring.is_self_vote(wallet, voter_x, idea["wallet"], idea["x_user_id"]):
        raise ServiceError(403, "self_vote", "You cannot vote for your own idea.")

    existing = await gf_store.get_vote(db, current, wallet)
    if existing and existing.get("signed_at") is not None and signed_at <= existing["signed_at"]:
        raise ServiceError(409, "stale_signature", "That signature is older than your current vote. Sign again.")
    if existing and existing["idea_id"] == idea_id:
        return {"idea_id": idea_id, "changed": False, "votes": await gf_store.vote_counts(db, current)}
    if await gf_store.vote_changes_since(db, wallet, now - timedelta(hours=1)) >= settings.GF_VOTE_CHANGES_PER_HOUR:
        raise ServiceError(429, "vote_rate_limited", f"At most {settings.GF_VOTE_CHANGES_PER_HOUR} vote changes per hour.")

    balance = await deps.chain.epc_balance(wallet)
    first_tx = await deps.chain.first_activity(wallet)
    reason = gf_scoring.vote_eligibility(balance_epc=balance, min_epc=settings.GF_VOTE_MIN_EPC, first_tx_at=first_tx, now=now,
                                         min_age_days=settings.GF_VOTE_MIN_WALLET_AGE_DAYS)
    if reason:
        status = 503 if reason in ("balance_unavailable", "wallet_age_unknown") else 403
        raise ServiceError(status, f"not_eligible", VOTE_MESSAGES[reason], {"reason": reason})

    await gf_store.upsert_vote(db, current, wallet, idea_id, signature, balance, signed_at)
    await db.commit()
    return {"idea_id": idea_id, "changed": True, "votes": await gf_store.vote_counts(db, current)}


VOTE_MESSAGES = {
    "balance_unavailable": "Could not read your EPC balance right now. Try again in a minute.",
    "balance_too_low": f"You need at least {settings.GF_VOTE_MIN_EPC:,.0f} EPC to vote.",
    "wallet_age_unknown": "Could not confirm your wallet age right now. Try again in a minute.",
    "wallet_too_new": f"Your wallet must be at least {settings.GF_VOTE_MIN_WALLET_AGE_DAYS} days old to vote.",
}


# ---------------------------------------------------------------------------
# Scoring (20:00 UTC) and announcement (20:05 UTC)
# ---------------------------------------------------------------------------

async def load_radar_clusters(db) -> tuple[list[dict], Optional[float]]:
    """Clusters (with centroids and saturation) of the latest Meta Radar run, and the chain baseline rate. Empty when
    the Radar has not run: every idea then gets the neutral narrative score instead of an invented one."""
    try:
        async with db.begin_nested():
            run = (await db.execute(text("SELECT run_id, baseline_rate FROM radar_runs ORDER BY run_at DESC LIMIT 1"))).mappings().first()
            if not run:
                return [], None
            rows = (await db.execute(text(
                "SELECT cluster_id, survival_rate, n_resolved, saturation, centroid_vec FROM radar_clusters "
                "WHERE run_id = :r AND centroid_vec IS NOT NULL"), {"r": run["run_id"]})).mappings().all()
            base = float(run["baseline_rate"]) if run["baseline_rate"] is not None else None
            return [dict(r) for r in rows], base
    except Exception as e:
        print(f"[GF] Meta Radar data unavailable: {type(e).__name__}", flush=True)
        return [], None


async def load_hour_rates(db) -> Optional[dict]:
    try:
        async with db.begin_nested():
            from app.api.endpoints import get_latest_model, serialize_model_run
            latest = await get_latest_model(db)
            return (serialize_model_run(latest) or {}).get("hour_rates") if latest else None
    except Exception as e:
        print(f"[GF] model hour rates unavailable: {type(e).__name__}", flush=True)
        return None


def _idea_input(row: dict, votes: int, clusters: list[dict], baseline: Optional[float]) -> tuple[gf_scoring.IdeaInput, Optional[str]]:
    emb = row.get("lore_embedding")
    cl = gf_scoring.nearest_cluster(emb, clusters)
    cluster = {"survival_rate": float(cl["survival_rate"]) if cl and cl.get("survival_rate") is not None else None,
               "n_resolved": cl.get("n_resolved") if cl else 0, "baseline_rate": baseline} if cl else None
    creator = gf_scoring.Creator(x_verified=row.get("x_verified"), x_created_at=row.get("x_created_at"),
                                 x_followers=row.get("x_followers"), wins_reached_30k=row.get("wins_reached_30k") or 0,
                                 wins_stalled=row.get("wins_stalled") or 0)
    inp = gf_scoring.IdeaInput(row["idea_id"], row["submitted_at"], creator, len(row["lore"]), cluster,
                               bool(cl and cl.get("saturation")), votes, row["x_user_id"], row["wallet"])
    return inp, (cl["cluster_id"] if cl else None)


async def score_round(db, deps: Deps, round_date: date, now: datetime) -> Optional[dict]:
    """
    Close the vote and score the pool. Re-checks every voter's EPC balance now (a flash-loan guard: a wallet that no longer
    holds the minimum, or whose balance cannot be read, does not count), scores all approved ideas, picks the winner under
    the 7 day cap and stores the scoreboard hash. Idempotent: a round already scored is left alone.
    """
    await _xact_lock(db, f"gf-round-{round_date}")
    rnd = await gf_store.ensure_round(db, round_date)
    if rnd["status"] not in ("submit", "review", "vote"):
        return None

    for v in await gf_store.votes_of_round(db, round_date):
        bal = await deps.chain.epc_balance(v["voter_wallet"])
        await gf_store.set_vote_close(db, round_date, v["voter_wallet"], bal, gf_scoring.counts_at_close(bal, settings.GF_VOTE_MIN_EPC))

    approved = await gf_store.ideas_of_round(db, round_date, ["approved"])
    counts = await gf_store.vote_counts(db, round_date, counted_only=True)
    clusters, baseline = await load_radar_clusters(db)
    inputs, cluster_of = [], {}
    for row in approved:
        inp, cid = _idea_input(row, counts.get(row["idea_id"], 0), clusters, baseline)
        inputs.append(inp)
        cluster_of[row["idea_id"]] = cid
    scored = gf_scoring.score_pool(inputs, now)
    for s in scored:
        s.detail["cluster_id"] = cluster_of.get(s.idea_id)
    await gf_store.write_scores(db, scored)

    boundary = gf_rounds.boundaries(round_date, deps.schedule)["vote_closes"]
    recent = [gf_scoring.RecentWin(w["x_user_id"], w["wallet"], w["at"])
              for w in await gf_store.recent_wins(db, boundary - timedelta(days=settings.GF_WIN_COOLDOWN_DAYS))]
    total_counted = sum(counts.values())
    result = gf_scoring.pick_winner(scored, recent, boundary, settings.GF_WIN_COOLDOWN_DAYS, total_counted)

    rows = scoreboard_rows(approved, scored, result.winner.idea_id if result.winner else None, reveal_winner=False)
    sha = gf_launch.scoreboard_hash(rows)
    n_submitted = await gf_store.count_ideas(db, round_date)
    fields: dict = {"status": "scoring", "votes_closed_at": now, "scoreboard_sha256": sha, "n_ideas": n_submitted,
                    "n_votes": total_counted}
    if result.winner:
        fields["winner_idea_id"] = result.winner.idea_id
    else:
        fields["no_launch_reason"] = result.no_launch_reason
    await gf_store.set_round(db, round_date, **fields)
    await db.commit()
    return {"round_date": round_date, "winner": result.winner, "no_launch_reason": result.no_launch_reason,
            "skipped": result.skipped, "scoreboard_sha256": sha, "n_ideas": n_submitted, "n_votes": total_counted}


def scoreboard_rows(ideas: list[dict], scored: list[gf_scoring.ScoredIdea], winner_id: Optional[str], reveal_winner: bool) -> list[dict]:
    by_id = {i["idea_id"]: i for i in ideas}
    out = []
    for rank, s in enumerate(gf_scoring.rank(scored), start=1):
        i = by_id[s.idea_id]
        out.append({"rank": rank, "idea_id": s.idea_id, "name": i["name"], "ticker": i["ticker"], "x_handle": i.get("x_handle"),
                    "credibility": s.credibility, "golem": s.golem, "vote": s.vote, "final": s.final, "votes": s.votes,
                    "image_url": i["image_url"], "winner": bool(reveal_winner and s.idea_id == winner_id),
                    "detail": s.detail})
    return out


async def announce_round(db, deps: Deps, round_date: date, now: datetime) -> Optional[dict]:
    """20:05 UTC: make the result public, schedule the launch slot and return what has to be posted."""
    await _xact_lock(db, f"gf-round-{round_date}")
    rnd = await gf_store.get_round(db, round_date)
    if rnd is None or rnd["status"] != "scoring":
        return None
    if now < gf_rounds.boundaries(round_date, deps.schedule)["announces"]:
        return None
    if not rnd["winner_idea_id"]:
        await gf_store.set_round(db, round_date, status="no_launch", announced_at=now)
        await db.commit()
        return {"kind": "no_launch", "round_date": round_date, "reason": rnd["no_launch_reason"],
                "n_ideas": rnd["n_ideas"], "n_votes": rnd["n_votes"]}

    winner = await gf_store.get_idea(db, rnd["winner_idea_id"])
    creator = await gf_store.creator_by_x(db, winner["x_user_id"])
    hour_rates = await load_hour_rates(db)
    due, reason = gf_launch.pick_launch_time(now, hour_rates, deps.schedule.launch_window_hours)
    await gf_store.set_round(db, round_date, status="announced", announced_at=now)
    await gf_store.set_last_win(db, winner["x_user_id"], now)
    await gf_store.create_launch_slot(db, winner["idea_id"], due, reason)
    await db.commit()
    return {"kind": "winner", "round_date": round_date, "idea": winner, "creator": creator, "launch_due_at": due,
            "launch_reason": reason, "n_ideas": rnd["n_ideas"], "n_votes": rnd["n_votes"]}


async def winner_post_text(db, announced: dict) -> str:
    if announced["kind"] == "no_launch":
        return gf_poster.render_no_launch_post(
            round_date=announced["round_date"],
            reason_text=gf_poster.NO_LAUNCH_TEXT.get(announced["reason"], "No launch today."),
            n_ideas=announced["n_ideas"] or 0, n_votes=announced["n_votes"] or 0)
    idea = announced["idea"]
    return gf_poster.render_winner_post(
        round_date=announced["round_date"], name=idea["name"], ticker=idea["ticker"],
        handle=(announced["creator"] or {}).get("x_handle") or "creator", final=float(idea["score_final"] or 0),
        credibility=float(idea["score_credibility"] or 0), golem=float(idea["score_golem"] or 0),
        vote=float(idea["score_vote"] or 0), n_ideas=announced["n_ideas"] or 0, n_votes=announced["n_votes"] or 0)


# ---------------------------------------------------------------------------
# Launch registration, verdicts, distributions
# ---------------------------------------------------------------------------

def extra_entry_for(idea_id: str, ca: str, launch_tx: str, why_hash: Optional[str], name: str) -> LaunchEntry:
    """The watcher entry of a community launch. Fees and burns come from the splitter events, not Blockscout."""
    why = {"window": None, "window_reason": None, "lore_summary": None, "model_run_id": None, "why_hash": why_hash}
    rules = {k: None for k in goforge_config.RULE_FIELDS}
    return LaunchEntry(id=idea_id, ca=ca, launch_tx=launch_tx, why=why, rules=rules, fee_router_address=None)


async def register_launch(db, deps: Deps, *, idea_id: str, ca: str, launch_tx: str, splitter: Optional[str],
                          registered_by: str, now: datetime) -> dict:
    """
    The team launched the winner on Pons and registers the result. The CA must be a contract, the idea must be a scheduled
    winner, and a CA never changes afterwards. The CA joins the trading exclude list at once.
    """
    try:
        reg = gf_launch.validate_registration(ca, launch_tx, splitter)
    except gf_launch.RegistrationError as e:
        raise ServiceError(422, "invalid_registration", str(e))
    slot = await gf_store.get_launch(db, idea_id)
    if slot is None:
        raise ServiceError(404, "no_launch_slot", "That idea has no scheduled launch (it did not win a round).")
    if slot["ca"] is not None:
        raise ServiceError(409, "already_registered", "That launch already has a CA.")
    for label, addr in (("ca", reg["ca"]), ("splitter_address", reg["splitter_address"])):
        if addr is None:
            continue
        code = await deps.chain.has_code(addr)
        if code is None:
            raise ServiceError(503, "chain_unavailable", "Could not verify the contract onchain right now. Try again.")
        if not code:
            raise ServiceError(422, "not_a_contract", f"{label} has no contract code on Robinhood Chain.")
    idea = await gf_store.get_idea(db, idea_id)
    rnd = await gf_store.get_round(db, idea["round_date"])
    await gf_store.set_launch_registered(db, idea_id, reg["ca"], reg["launch_tx"], now, reg["splitter_address"])
    cursor = await deps.chain.latest_block() if reg["splitter_address"] else None
    if cursor is not None:
        await gf_store.set_launch_cursor(db, idea_id, max(0, cursor - 10))
    # the watcher tracks it like any GoForge launch: market data, holders, peak MC and the 48h verdict
    await db.execute(text("INSERT INTO goforge_launches (id, ca, launch_tx) VALUES (:i, :c, :t) ON CONFLICT (id) DO NOTHING"),
                     {"i": idea_id, "c": reg["ca"], "t": reg["launch_tx"]})
    await gf_store.set_round(db, idea["round_date"], status="launched")
    await db.commit()
    entry = extra_entry_for(idea_id, reg["ca"], reg["launch_tx"], rnd["scoreboard_sha256"] if rnd else None, idea["name"])
    goforge_config.register_runtime_entry(entry)
    return {"idea_id": idea_id, **reg, "registered_by": registered_by, "entry": entry}


async def load_registered_entries(db) -> list[LaunchEntry]:
    """Watcher entries for every community launch already registered (called once at startup)."""
    out = []
    for l in await gf_store.launches_with_ca(db):
        rnd = await gf_store.get_round(db, l["round_date"])
        out.append(extra_entry_for(l["idea_id"], l["ca"], l["launch_tx"], rnd["scoreboard_sha256"] if rnd else None, l["name"]))
    return out


async def sync_verdicts(db, deps: Deps) -> list[dict]:
    """Copy locked 48h verdicts into the registry, update the creator's track record once, return what to announce."""
    out = []
    for v in await gf_store.unsynced_verdicts(db):
        await gf_store.copy_verdict(db, v["idea_id"], v["verdict"], v["verdict_at"], float(v["peak_mc_usd"] or 0))
        if v["x_user_id"]:
            await gf_store.add_creator_result(db, v["x_user_id"], v["verdict"])
        launch = await gf_store.get_launch(db, v["idea_id"])
        out.append({**v, "fees_to_creator": float(launch["fees_to_creator"] or 0), "epc_burned": float(launch["epc_burned"] or 0)})
    if out:
        await db.commit()
    return out


async def index_distributions(db, deps: Deps) -> list[dict]:
    """Read the splitters' `Distributed` events since each cursor and store them (idempotent on tx hash + log index)."""
    new: list[dict] = []
    head = await deps.chain.latest_block()
    if head is None:
        return new
    for l in await gf_store.launches_with_ca(db):
        if not l["splitter_address"]:
            continue
        frm = int(l["dist_block"]) if l["dist_block"] is not None else max(0, head - 5000)
        to = min(head, frm + 20_000)
        if to < frm:
            continue
        for d in await deps.chain.distributions(l["splitter_address"], frm, to):
            if await gf_store.insert_distribution(db, {**{k: d[k] for k in ("tx_hash", "log_index", "block", "at", "creator_wei", "burn_wei", "epc_burned_wei")},
                                                       "idea_id": l["idea_id"]}):
                new.append({**d, "idea_id": l["idea_id"]})
        await gf_store.set_launch_cursor(db, l["idea_id"], to + 1)
        await gf_store.refresh_launch_totals(db, l["idea_id"], 18, settings.GF_EPC_DECIMALS)
    if new or head:
        await db.commit()
    return new


async def keeper_distribute(db, deps: Deps, now: datetime) -> list[str]:
    """Call distribute() on each splitter that holds ETH and was last distributed more than the interval ago."""
    sent = []
    if not settings.GF_KEEPER_PRIVATE_KEY:
        return sent
    for l in await gf_store.launches_with_ca(db):
        if not l["splitter_address"]:
            continue
        last = l.get("last_distribute_at")
        if last and now - last < timedelta(hours=settings.GF_DISTRIBUTE_INTERVAL_HOURS):
            continue
        bal = await deps.chain.eth_balance(l["splitter_address"])
        if not bal:
            continue
        try:
            tx = await deps.chain.send_distribute(l["splitter_address"], 0)
        except Exception as e:
            print(f"[GF] distribute failed for {l['idea_id']}: {type(e).__name__} {e}", flush=True)
            continue
        if tx:
            await gf_store.set_last_distribute(db, l["idea_id"], now)
            sent.append(tx)
    if sent:
        await db.commit()
    return sent


# ---------------------------------------------------------------------------
# X posts
# ---------------------------------------------------------------------------

MAX_POST_ATTEMPTS = 3


async def post_once(db, deps: Deps, kind: str, ref: str, body: str) -> str:
    """Queue a post once and try to send it. Without GF_X_POST_ENABLED it is recorded as a dry run, never sent."""
    claimed = await gf_store.claim_post(db, kind, ref, body)
    await db.commit()
    if not claimed:
        return "duplicate"      # already queued or sent: a restart or a second caller never posts twice
    return await _send_post(db, deps, kind, ref, body)


async def _send_post(db, deps: Deps, kind: str, ref: str, body: str) -> str:
    if not settings.GF_X_POST_ENABLED or deps.poster is None:
        await gf_store.finish_post(db, kind, ref, "dry_run")
        await db.commit()
        return "dry_run"
    res = await deps.poster.post_desk_tweet(body, f"goforge_{kind}")
    status = {"sent": "posted", "dry_run": "dry_run"}.get(res.get("status"), "failed")
    await gf_store.finish_post(db, kind, ref, status, tweet_id=res.get("tweet_id"), error=res.get("error_message"))
    await db.commit()
    return status


async def retry_failed_posts(db, deps: Deps) -> int:
    rows = (await db.execute(text(
        "SELECT kind, ref, text FROM gf_posts WHERE status IN ('pending','failed') AND attempts < :m ORDER BY created_at"),
        {"m": MAX_POST_ATTEMPTS})).mappings().all()
    for r in rows:
        await _send_post(db, deps, r["kind"], r["ref"], r["text"])
    return len(rows)
