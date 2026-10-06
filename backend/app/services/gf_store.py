"""
GoForge Registry data access. Every function takes an open session `db`; none of them commits unless it says so, so the
caller (endpoint or worker) decides the transaction boundary. Plain SQL, no business rules: those live in the pure modules.
"""
import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import text


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------

async def ensure_round(db, round_date: date) -> dict:
    await db.execute(text("INSERT INTO gf_rounds (round_date, status) VALUES (:d, 'submit') ON CONFLICT DO NOTHING"),
                     {"d": round_date})
    return await get_round(db, round_date)


async def get_round(db, round_date: date) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_rounds WHERE round_date = :d"), {"d": round_date})).mappings().first())


async def set_round(db, round_date: date, **fields) -> None:
    allowed = {"status", "winner_idea_id", "n_ideas", "n_votes", "announced_at", "no_launch_reason", "votes_closed_at",
               "scoreboard_sha256"}
    assert set(fields) <= allowed, set(fields) - allowed
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    await db.execute(text(f"UPDATE gf_rounds SET {sets} WHERE round_date = :d"), {**fields, "d": round_date})


async def open_rounds_before(db, round_date: date) -> list[dict]:
    """Rounds that never reached 'announced' / 'launched' / 'no_launch': a worker outage leaves these behind."""
    rows = (await db.execute(text(
        "SELECT * FROM gf_rounds WHERE round_date < :d AND status IN ('submit','review','vote','scoring') ORDER BY round_date"),
        {"d": round_date})).mappings().all()
    return [dict(r) for r in rows]


async def recent_rounds(db, limit: int = 14) -> list[dict]:
    rows = (await db.execute(text("SELECT * FROM gf_rounds ORDER BY round_date DESC LIMIT :n"), {"n": limit})).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Creators
# ---------------------------------------------------------------------------

async def creator_by_wallet(db, wallet: str) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_creators WHERE wallet = :w"), {"w": wallet.lower()})).mappings().first())


async def creator_by_x(db, x_user_id: str) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_creators WHERE x_user_id = :x"), {"x": x_user_id})).mappings().first())


async def upsert_creator(db, x: dict, wallet: str) -> None:
    """Link (or refresh) a verified X account to a wallet. The X fields come from the X API, never from the client."""
    await db.execute(text(
        "INSERT INTO gf_creators (x_user_id, x_handle, x_verified, x_created_at, x_followers, wallet, updated_at) "
        "VALUES (:id, :h, :v, :c, :f, :w, now()) "
        "ON CONFLICT (x_user_id) DO UPDATE SET x_handle = EXCLUDED.x_handle, x_verified = EXCLUDED.x_verified, "
        "x_created_at = EXCLUDED.x_created_at, x_followers = EXCLUDED.x_followers, wallet = EXCLUDED.wallet, "
        "updated_at = now()"),
        {"id": x["x_user_id"], "h": x.get("x_handle"), "v": x.get("x_verified"), "c": x.get("x_created_at"),
         "f": x.get("x_followers"), "w": wallet.lower()})


async def log_wallet_change(db, x_user_id: str, old: Optional[str], new: Optional[str], reason: str, by: str) -> None:
    await db.execute(text(
        "INSERT INTO gf_wallet_changes (x_user_id, old_wallet, new_wallet, reason, changed_by) VALUES (:x, :o, :n, :r, :b)"),
        {"x": x_user_id, "o": old, "n": new, "r": reason, "b": by})


async def change_wallet(db, x_user_id: str, new_wallet: str, reason: str, by: str) -> None:
    """The manual wallet replacement: logged first, then applied. Not reachable from any user endpoint."""
    cur = await creator_by_x(db, x_user_id)
    if cur is None:
        raise LookupError("no such creator")
    await log_wallet_change(db, x_user_id, cur["wallet"], new_wallet.lower(), reason, by)
    await db.execute(text("UPDATE gf_creators SET wallet = :w, updated_at = now() WHERE x_user_id = :x"),
                     {"w": new_wallet.lower(), "x": x_user_id})


async def recent_wins(db, since: datetime) -> list[dict]:
    """Winners of rounds since `since`, with the identities used (for the 7 day cap)."""
    rows = (await db.execute(text(
        "SELECT i.x_user_id, i.wallet, r.announced_at AS at FROM gf_rounds r JOIN gf_ideas i ON i.idea_id = r.winner_idea_id "
        "WHERE r.announced_at IS NOT NULL AND r.announced_at > :s"), {"s": since})).mappings().all()
    return [dict(r) for r in rows]


async def add_creator_result(db, x_user_id: str, verdict: str) -> None:
    col = "wins_reached_30k" if verdict == "reached_30k" else "wins_stalled"
    await db.execute(text(f"UPDATE gf_creators SET {col} = COALESCE({col}, 0) + 1, updated_at = now() WHERE x_user_id = :x"),
                     {"x": x_user_id})


async def set_last_win(db, x_user_id: str, at: datetime) -> None:
    await db.execute(text("UPDATE gf_creators SET last_win_at = :a, updated_at = now() WHERE x_user_id = :x"),
                     {"a": at, "x": x_user_id})


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------

async def count_ideas(db, round_date: date) -> int:
    return int((await db.execute(text("SELECT count(*) FROM gf_ideas WHERE round_date = :d"), {"d": round_date})).scalar() or 0)


async def wallet_idea_today(db, wallet: str, round_date: date) -> bool:
    return bool((await db.execute(text("SELECT 1 FROM gf_ideas WHERE wallet = :w AND round_date = :d LIMIT 1"),
                                  {"w": wallet.lower(), "d": round_date})).first())


async def fee_tx_used(db, tx_hash: str) -> bool:
    return bool((await db.execute(text("SELECT 1 FROM gf_ideas WHERE fee_tx = :t LIMIT 1"), {"t": tx_hash.lower()})).first())


async def insert_idea(db, idea: dict) -> None:
    """The fee tx is UNIQUE in the table: a concurrent reuse fails here even if the pre-check raced."""
    await db.execute(text(
        "INSERT INTO gf_ideas (idea_id, round_date, x_user_id, wallet, name, ticker, lore, image_url, image_sha256, "
        "fee_tx, status, reject_reason, auto_flags, lore_embedding, submitted_at) "
        "VALUES (:idea_id, :round_date, :x_user_id, :wallet, :name, :ticker, :lore, :image_url, :image_sha256, :fee_tx, "
        ":status, :reject_reason, CAST(:auto_flags AS JSONB), CAST(:lore_embedding AS DOUBLE PRECISION[]), :submitted_at)"),
        {**idea, "auto_flags": json.dumps(idea.get("auto_flags") or {}), "lore_embedding": idea.get("lore_embedding")})


async def get_idea(db, idea_id: str) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_ideas WHERE idea_id = :i"), {"i": idea_id})).mappings().first())


async def ideas_of_round(db, round_date: date, statuses: Optional[Sequence[str]] = None) -> list[dict]:
    q = ("SELECT i.*, c.x_handle, c.x_verified, c.x_created_at, c.x_followers, c.wins_reached_30k, c.wins_stalled "
         "FROM gf_ideas i LEFT JOIN gf_creators c ON c.x_user_id = i.x_user_id WHERE i.round_date = :d")
    params: dict = {"d": round_date}
    if statuses:
        q += " AND i.status = ANY(:st)"
        params["st"] = list(statuses)
    rows = (await db.execute(text(q + " ORDER BY i.submitted_at, i.idea_id"), params)).mappings().all()
    return [dict(r) for r in rows]


async def ideas_of_wallet(db, wallet: str, limit: int = 30) -> list[dict]:
    rows = (await db.execute(text("SELECT * FROM gf_ideas WHERE wallet = :w ORDER BY submitted_at DESC LIMIT :n"),
                             {"w": wallet.lower(), "n": limit})).mappings().all()
    return [dict(r) for r in rows]


async def pending_review(db, limit: int = 200) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT i.*, c.x_handle FROM gf_ideas i LEFT JOIN gf_creators c ON c.x_user_id = i.x_user_id "
        "WHERE i.status = 'pending_review' ORDER BY i.submitted_at LIMIT :n"), {"n": limit})).mappings().all()
    return [dict(r) for r in rows]


async def review_idea(db, idea_id: str, status: str, reason: Optional[str], by: str) -> bool:
    """approve / reject. Refused once the idea is the winner of a round or launched (those facts are public)."""
    assert status in ("approved", "rejected")
    res = await db.execute(text(
        "UPDATE gf_ideas SET status = :s, reject_reason = :r, reviewed_at = now(), reviewed_by = :b "
        "WHERE idea_id = :i AND NOT EXISTS (SELECT 1 FROM gf_rounds WHERE winner_idea_id = :i)"),
        {"s": status, "r": reason if status == "rejected" else None, "b": by, "i": idea_id})
    return res.rowcount == 1


async def same_day_others(db, round_date: date) -> list[dict]:
    """Earlier ideas of the same round that count for duplicate detection (anything not already rejected)."""
    rows = (await db.execute(text(
        "SELECT idea_id, name, ticker, lore_embedding FROM gf_ideas WHERE round_date = :d AND status <> 'rejected' "
        "ORDER BY submitted_at"), {"d": round_date})).mappings().all()
    return [dict(r) for r in rows]


async def existing_tickers(db, ticker: str) -> list[str]:
    """Tickers already taken on Robinhood Chain (tokens table) or by an idea that was launched."""
    t = ticker.upper()
    out = [r[0] for r in (await db.execute(text(
        "SELECT symbol FROM tokens WHERE upper(symbol) = :t AND chain = 'robinhood' LIMIT 5"), {"t": t})).all()]
    out += [r[0] for r in (await db.execute(text(
        "SELECT i.ticker FROM gf_ideas i JOIN gf_launches l ON l.idea_id = i.idea_id WHERE upper(i.ticker) = :t LIMIT 5"),
        {"t": t})).all()]
    return out


async def write_scores(db, scored: Sequence) -> None:
    for s in scored:
        await db.execute(text(
            "UPDATE gf_ideas SET score_credibility = :c, score_golem = :g, score_vote = :v, score_final = :f, "
            "votes_counted = :n, radar_cluster_id = :cl WHERE idea_id = :i"),
            {"c": s.credibility, "g": s.golem, "v": s.vote, "f": s.final, "n": s.votes, "i": s.idea_id,
             "cl": s.detail.get("cluster_id")})


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------

async def get_vote(db, round_date: date, wallet: str) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_votes WHERE round_date = :d AND voter_wallet = :w"),
                                  {"d": round_date, "w": wallet.lower()})).mappings().first())


async def upsert_vote(db, round_date: date, wallet: str, idea_id: str, signature: str, balance: float, signed_at: int) -> None:
    """One vote per wallet per round: the last one replaces the earlier one."""
    await db.execute(text(
        "INSERT INTO gf_votes (round_date, voter_wallet, idea_id, signature, epc_balance_vote, signed_at, voted_at) "
        "VALUES (:d, :w, :i, :s, :b, :t, now()) "
        "ON CONFLICT (round_date, voter_wallet) DO UPDATE SET idea_id = EXCLUDED.idea_id, signature = EXCLUDED.signature, "
        "epc_balance_vote = EXCLUDED.epc_balance_vote, signed_at = EXCLUDED.signed_at, voted_at = now()"),
        {"d": round_date, "w": wallet.lower(), "i": idea_id, "s": signature, "b": balance, "t": signed_at})
    await db.execute(text("INSERT INTO gf_vote_events (voter_wallet, round_date, idea_id) VALUES (:w, :d, :i)"),
                     {"w": wallet.lower(), "d": round_date, "i": idea_id})


async def vote_changes_since(db, wallet: str, since: datetime) -> int:
    return int((await db.execute(text("SELECT count(*) FROM gf_vote_events WHERE voter_wallet = :w AND at > :s"),
                                 {"w": wallet.lower(), "s": since})).scalar() or 0)


async def vote_counts(db, round_date: date, counted_only: bool = False) -> dict[str, int]:
    q = "SELECT idea_id, count(*) FROM gf_votes WHERE round_date = :d"
    if counted_only:
        q += " AND counted IS TRUE"
    rows = (await db.execute(text(q + " GROUP BY idea_id"), {"d": round_date})).all()
    return {r[0]: int(r[1]) for r in rows}


async def votes_of_round(db, round_date: date) -> list[dict]:
    rows = (await db.execute(text("SELECT * FROM gf_votes WHERE round_date = :d ORDER BY voted_at, voter_wallet"),
                             {"d": round_date})).mappings().all()
    return [dict(r) for r in rows]


async def set_vote_close(db, round_date: date, wallet: str, balance_close: Optional[float], counted: bool) -> None:
    await db.execute(text("UPDATE gf_votes SET epc_balance_close = :b, counted = :c WHERE round_date = :d AND voter_wallet = :w"),
                     {"b": balance_close, "c": counted, "d": round_date, "w": wallet.lower()})


# ---------------------------------------------------------------------------
# Launches and distributions
# ---------------------------------------------------------------------------

async def create_launch_slot(db, idea_id: str, due_at: datetime, reason: str) -> None:
    await db.execute(text(
        "INSERT INTO gf_launches (idea_id, launch_due_at, launch_hour_reason) VALUES (:i, :d, :r) ON CONFLICT DO NOTHING"),
        {"i": idea_id, "d": due_at, "r": reason})


async def get_launch(db, idea_id: str) -> Optional[dict]:
    return _row((await db.execute(text("SELECT * FROM gf_launches WHERE idea_id = :i"), {"i": idea_id})).mappings().first())


async def set_launch_registered(db, idea_id: str, ca: str, launch_tx: str, launched_at: datetime,
                                splitter: Optional[str]) -> bool:
    res = await db.execute(text(
        "UPDATE gf_launches SET ca = :ca, launch_tx = :tx, launched_at = :at, splitter_address = :sp "
        "WHERE idea_id = :i AND ca IS NULL"), {"ca": ca, "tx": launch_tx, "at": launched_at, "sp": splitter, "i": idea_id})
    return res.rowcount == 1


async def launches_with_ca(db) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT l.*, i.name, i.ticker, i.round_date, i.x_user_id, i.wallet, i.image_url, i.lore, c.x_handle "
        "FROM gf_launches l JOIN gf_ideas i ON i.idea_id = l.idea_id LEFT JOIN gf_creators c ON c.x_user_id = i.x_user_id "
        "WHERE l.ca IS NOT NULL ORDER BY l.launched_at DESC"))).mappings().all()
    return [dict(r) for r in rows]


async def launches_missing_post(db, kind: str) -> list[dict]:
    """Registered launches that have no X post of this kind yet."""
    rows = (await db.execute(text(
        "SELECT l.idea_id, l.ca, i.name, i.ticker FROM gf_launches l JOIN gf_ideas i ON i.idea_id = l.idea_id "
        "WHERE l.ca IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gf_posts p WHERE p.kind = :k AND p.ref = l.idea_id)"),
        {"k": kind})).mappings().all()
    return [dict(r) for r in rows]


async def pending_launch_slots(db) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT l.*, i.name, i.ticker, i.round_date, i.x_user_id, i.wallet, c.x_handle FROM gf_launches l "
        "JOIN gf_ideas i ON i.idea_id = l.idea_id LEFT JOIN gf_creators c ON c.x_user_id = i.x_user_id "
        "WHERE l.ca IS NULL ORDER BY l.launch_due_at"))).mappings().all()
    return [dict(r) for r in rows]


async def unsynced_verdicts(db) -> list[dict]:
    """Launches whose 48h verdict the watcher locked but the registry has not yet copied (and announced)."""
    rows = (await db.execute(text(
        "SELECT l.idea_id, g.verdict, g.verdict_at, g.peak_mc_usd, i.x_user_id, i.name, i.ticker FROM gf_launches l "
        "JOIN goforge_launches g ON g.id = l.idea_id JOIN gf_ideas i ON i.idea_id = l.idea_id "
        "WHERE l.verdict = 'pending' AND g.verdict <> 'pending'"))).mappings().all()
    return [dict(r) for r in rows]


async def copy_verdict(db, idea_id: str, verdict: str, verdict_at: datetime, peak: float) -> None:
    await db.execute(text(
        "UPDATE gf_launches SET verdict = :v, verdict_at = :a, peak_mc_usd = :p, verdict_synced = TRUE WHERE idea_id = :i"),
        {"v": verdict, "a": verdict_at, "p": peak, "i": idea_id})


async def insert_distribution(db, d: dict) -> bool:
    res = await db.execute(text(
        "INSERT INTO gf_distributions (tx_hash, log_index, idea_id, block, at, creator_wei, burn_wei, epc_burned_wei) "
        "VALUES (:tx_hash, :log_index, :idea_id, :block, :at, :creator_wei, :burn_wei, :epc_burned_wei) ON CONFLICT DO NOTHING"), d)
    return res.rowcount == 1


async def refresh_launch_totals(db, idea_id: str, eth_decimals: int = 18, epc_decimals: int = 18) -> None:
    """fees_to_creator is in ETH and epc_burned in whole EPC, summed from the indexed Distributed events."""
    await db.execute(text(
        "UPDATE gf_launches SET "
        "fees_to_creator = COALESCE((SELECT sum(creator_wei) FROM gf_distributions WHERE idea_id = :i), 0) / CAST(:e AS NUMERIC), "
        "epc_burned = COALESCE((SELECT sum(epc_burned_wei) FROM gf_distributions WHERE idea_id = :i), 0) / CAST(:p AS NUMERIC) "
        "WHERE idea_id = :i"), {"i": idea_id, "e": 10 ** eth_decimals, "p": 10 ** epc_decimals})


async def distributions_of(db, idea_id: str) -> list[dict]:
    rows = (await db.execute(text("SELECT * FROM gf_distributions WHERE idea_id = :i ORDER BY block, log_index"),
                             {"i": idea_id})).mappings().all()
    return [dict(r) for r in rows]


async def set_launch_cursor(db, idea_id: str, block: int) -> None:
    await db.execute(text("UPDATE gf_launches SET dist_block = :b WHERE idea_id = :i"), {"b": block, "i": idea_id})


async def set_last_distribute(db, idea_id: str, at: datetime) -> None:
    await db.execute(text("UPDATE gf_launches SET last_distribute_at = :a WHERE idea_id = :i"), {"a": at, "i": idea_id})


async def totals(db) -> dict:
    row = (await db.execute(text(
        "SELECT count(*) AS launches, COALESCE(sum(epc_burned), 0) AS epc_burned, COALESCE(sum(fees_to_creator), 0) AS fees "
        "FROM gf_launches WHERE ca IS NOT NULL"))).mappings().one()
    ideas = (await db.execute(text(
        "SELECT count(*) AS n, COALESCE(sum((auto_flags->>'fee_epc_burned')::numeric), 0) AS burned FROM gf_ideas"))).mappings().one()
    return {"launches": int(row["launches"]), "epc_burned_from_fees": float(row["epc_burned"]),
            "epc_burned_from_submit_fees": float(ideas["burned"]), "fees_paid_to_creators_eth": float(row["fees"]),
            "ideas_submitted": int(ideas["n"])}


# ---------------------------------------------------------------------------
# Nonces, OAuth state, posts
# ---------------------------------------------------------------------------

async def store_nonce(db, nonce: str) -> None:
    await db.execute(text("INSERT INTO gf_nonces (nonce) VALUES (:n)"), {"n": nonce})


async def consume_nonce(db, nonce: str, max_age: timedelta = timedelta(minutes=10)) -> bool:
    """True once: the first use of a fresh nonce. A replayed or unknown nonce returns False."""
    res = await db.execute(text(
        "UPDATE gf_nonces SET used_at = now() WHERE nonce = :n AND used_at IS NULL AND created_at > :c"),
        {"n": nonce, "c": datetime.now(timezone.utc) - max_age})
    return res.rowcount == 1


async def store_oauth_state(db, state: str, wallet: str, verifier: str) -> None:
    await db.execute(text("INSERT INTO gf_oauth_states (state, wallet, code_verifier) VALUES (:s, :w, :v)"),
                     {"s": state, "w": wallet.lower(), "v": verifier})


async def pop_oauth_state(db, state: str, max_age: timedelta = timedelta(minutes=10)) -> Optional[dict]:
    """Single use: the state row is deleted as it is read."""
    row = (await db.execute(text(
        "DELETE FROM gf_oauth_states WHERE state = :s AND created_at > :c RETURNING wallet, code_verifier"),
        {"s": state, "c": datetime.now(timezone.utc) - max_age})).mappings().first()
    return _row(row)


async def purge_stale(db) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    await db.execute(text("DELETE FROM gf_nonces WHERE created_at < :c"), {"c": cutoff})
    await db.execute(text("DELETE FROM gf_oauth_states WHERE created_at < :c"), {"c": cutoff})


async def claim_post(db, kind: str, ref: str, body: str) -> bool:
    """Register a post once. False when this kind/ref was already queued (so a restart never double-posts)."""
    res = await db.execute(text(
        "INSERT INTO gf_posts (kind, ref, text, status) VALUES (:k, :r, :t, 'pending') ON CONFLICT DO NOTHING"),
        {"k": kind, "r": ref, "t": body})
    return res.rowcount == 1


async def finish_post(db, kind: str, ref: str, status: str, tweet_id: Optional[str] = None, error: Optional[str] = None) -> None:
    await db.execute(text(
        "UPDATE gf_posts SET status = :s, tweet_id = :t, error = :e, attempts = attempts + 1, "
        "posted_at = CASE WHEN :s IN ('posted','dry_run') THEN now() ELSE posted_at END WHERE kind = :k AND ref = :r"),
        {"s": status, "t": tweet_id, "e": error, "k": kind, "r": ref})
