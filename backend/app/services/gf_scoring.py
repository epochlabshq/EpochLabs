"""
GoForge Registry scoring (brief section 5). Pure functions: ideas and creators in, scores out.

    final = 0.40 x credibility + 0.30 x golem + 0.30 x vote

Every component is on 0-100. Components are rounded to 2 decimals first and the final score is computed from those
rounded numbers, so anyone can recompute a published score from the published table. Credibility and Golem are
absolute scores from their formulas; only the vote component is relative (to the most-voted idea of the day).
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

W_CREDIBILITY, W_GOLEM, W_VOTE = 0.40, 0.30, 0.30
# Credibility: four equal sub-components (10% each of the 40%)
ACCOUNT_AGE_FULL_DAYS = 730
FOLLOWERS_FULL_LOG10 = 5          # 100K followers
TRACK_BASE, TRACK_WIN, TRACK_STALL = 50.0, 25.0, -15.0
# Golem: narrative survival 20 of 30, lore quality 10 of 30
NARRATIVE_SHARE, LORE_SHARE = 20 / 30, 10 / 30
SATURATION_PENALTY = 20.0
NEUTRAL = 50.0
NARRATIVE_MIN_RESOLVED = 20
NARRATIVE_MAX_COSINE_DISTANCE = 0.6
LORE_FULL_FROM, LORE_FLAT_TO, LORE_END_AT, LORE_END_SCORE = 250, 600, 1000, 70.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def r2(v: float) -> float:
    return round(v + 1e-12, 2)


# ---------------------------------------------------------------------------
# Creator credibility (40%)
# ---------------------------------------------------------------------------

def score_verified(verified: Optional[bool]) -> float:
    return 100.0 if verified else 0.0


def score_account_age(created_at: Optional[datetime], now: datetime) -> float:
    if created_at is None:
        return 0.0
    days = max(0.0, (now - created_at).total_seconds() / 86400)
    return min(days / ACCOUNT_AGE_FULL_DAYS, 1.0) * 100


def score_followers(followers: Optional[int]) -> float:
    if not followers or followers < 0:
        return 0.0
    return min(math.log10(followers + 1) / FOLLOWERS_FULL_LOG10, 1.0) * 100


def score_track_record(wins_reached_30k: int, wins_stalled: int) -> float:
    """A new creator starts at 50; every earlier win that reached $30K is +25, every stalled one -15."""
    return _clamp(TRACK_BASE + TRACK_WIN * (wins_reached_30k or 0) + TRACK_STALL * (wins_stalled or 0))


@dataclass(frozen=True)
class Creator:
    x_verified: Optional[bool] = None
    x_created_at: Optional[datetime] = None
    x_followers: Optional[int] = None
    wins_reached_30k: int = 0
    wins_stalled: int = 0


def credibility(c: Creator, now: datetime) -> dict:
    subs = {
        "verified": score_verified(c.x_verified),
        "account_age": score_account_age(c.x_created_at, now),
        "followers": score_followers(c.x_followers),
        "track_record": score_track_record(c.wins_reached_30k, c.wins_stalled),
    }
    return {"score": sum(subs.values()) / len(subs), "parts": subs}


# ---------------------------------------------------------------------------
# Golem score (30%)
# ---------------------------------------------------------------------------

def narrative_score(cluster: Optional[dict], min_resolved: int = NARRATIVE_MIN_RESOLVED) -> tuple[float, str]:
    """
    Narrative survival: the idea's Meta Radar cluster survival rate against the chain baseline, lift 1 = 50 and
    lift >= 2 = 100. No cluster, a thin cluster (n_resolved below the minimum) or no baseline is neutral, 50.
    cluster = {survival_rate, n_resolved, baseline_rate} (rates as fractions or percent, same unit for both).
    """
    if not cluster:
        return NEUTRAL, "no_cluster"
    if (cluster.get("n_resolved") or 0) < min_resolved:
        return NEUTRAL, "thin_cluster"
    rate, base = cluster.get("survival_rate"), cluster.get("baseline_rate")
    if rate is None or not base:
        return NEUTRAL, "no_baseline"
    return _clamp(float(rate) / float(base) * 50.0), "ok"


def lore_quality(length: int) -> float:
    """Lore length is a strong signal in the model, so a fuller lore scores higher, up to a cap: 0 at 50 characters,
    100 at 250, flat to 600, then easing down to 70 at 1,000 so an essay does not win by length alone."""
    if length <= 50:
        return 0.0
    if length < LORE_FULL_FROM:
        return (length - 50) / (LORE_FULL_FROM - 50) * 100
    if length <= LORE_FLAT_TO:
        return 100.0
    if length >= LORE_END_AT:
        return LORE_END_SCORE
    return 100 - (100 - LORE_END_SCORE) * (length - LORE_FLAT_TO) / (LORE_END_AT - LORE_FLAT_TO)


def golem_score(narrative: float, lore_len: int, saturated: bool) -> dict:
    lore = lore_quality(lore_len)
    base = NARRATIVE_SHARE * narrative + LORE_SHARE * lore
    score = _clamp(base - (SATURATION_PENALTY if saturated else 0.0))
    return {"score": score, "parts": {"narrative": narrative, "lore_quality": lore, "saturation_penalty":
                                       SATURATION_PENALTY if saturated else 0.0}}


def nearest_cluster(vec: Optional[Sequence[float]], clusters: Iterable[dict],
                    max_distance: float = NARRATIVE_MAX_COSINE_DISTANCE) -> Optional[dict]:
    """The Meta Radar cluster whose centroid is closest to the lore vector (cosine distance), or None when none is close
    enough: an idea that fits no narrative gets the neutral score instead of being forced into one."""
    if vec is None:
        return None
    nv = math.sqrt(sum(x * x for x in vec))
    if not nv:
        return None
    best, best_d = None, None
    for c in clusters:
        cv = c.get("centroid_vec")
        if not cv or len(cv) != len(vec):
            continue
        nc = math.sqrt(sum(x * x for x in cv))
        if not nc:
            continue
        d = 1 - sum(a * b for a, b in zip(vec, cv)) / (nv * nc)
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best if best is not None and best_d <= max_distance else None


# ---------------------------------------------------------------------------
# Vote (30%) and the combined score
# ---------------------------------------------------------------------------

def vote_score(votes: int, max_votes: int) -> float:
    return 0.0 if max_votes <= 0 else votes / max_votes * 100


def final_score(cred: float, golem: float, vote: float) -> float:
    """From the rounded components, so the published table reproduces the published final score."""
    return r2(W_CREDIBILITY * r2(cred) + W_GOLEM * r2(golem) + W_VOTE * r2(vote))


@dataclass
class ScoredIdea:
    idea_id: str
    submitted_at: datetime
    credibility: float
    golem: float
    vote: float
    final: float
    votes: int
    x_user_id: Optional[str] = None
    wallet: Optional[str] = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IdeaInput:
    idea_id: str
    submitted_at: datetime
    creator: Creator
    lore_len: int
    cluster: Optional[dict]
    saturated: bool
    votes: int
    x_user_id: Optional[str] = None
    wallet: Optional[str] = None


def score_pool(ideas: Sequence[IdeaInput], now: datetime) -> list[ScoredIdea]:
    """Score every approved idea of the day. Vote is relative to the most-voted idea of this pool."""
    max_votes = max((i.votes for i in ideas), default=0)
    out = []
    for i in ideas:
        cred = credibility(i.creator, now)
        narr, narr_note = narrative_score(i.cluster)
        gol = golem_score(narr, i.lore_len, i.saturated)
        v = r2(vote_score(i.votes, max_votes))
        c, g = r2(cred["score"]), r2(gol["score"])
        out.append(ScoredIdea(i.idea_id, i.submitted_at, c, g, v, final_score(c, g, v), i.votes, i.x_user_id, i.wallet,
                              {"credibility": {k: r2(x) for k, x in cred["parts"].items()},
                               "golem": {k: r2(x) for k, x in gol["parts"].items()}, "narrative_note": narr_note}))
    return out


# ---------------------------------------------------------------------------
# Winner
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecentWin:
    x_user_id: Optional[str]
    wallet: Optional[str]
    at: datetime


@dataclass
class WinnerResult:
    winner: Optional[ScoredIdea]
    no_launch_reason: Optional[str] = None
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (idea_id, why) for the transparency note


def rank(scored: Sequence[ScoredIdea]) -> list[ScoredIdea]:
    """Highest final score first; on a tie the idea submitted earlier wins (idea id as the last, stable tie-break)."""
    return sorted(scored, key=lambda s: (-s.final, s.submitted_at, s.idea_id))


def pick_winner(scored: Sequence[ScoredIdea], recent_wins: Iterable[RecentWin], round_close: datetime,
                cooldown_days: int, total_counted_votes: int) -> WinnerResult:
    """
    Highest score, except a creator (x_user_id or wallet) who already won within `cooldown_days` is skipped for the
    next rank. No approved idea, or not a single counted vote, means no launch that day, with the reason.
    """
    if not scored:
        return WinnerResult(None, "no_approved_ideas")
    if total_counted_votes <= 0:
        return WinnerResult(None, "no_votes")
    since = round_close - timedelta(days=cooldown_days)
    wins = [w for w in recent_wins if w.at > since]
    blocked_users = {w.x_user_id for w in wins if w.x_user_id}
    blocked_wallets = {w.wallet.lower() for w in wins if w.wallet}
    skipped: list[tuple[str, str]] = []
    for s in rank(scored):
        if (s.x_user_id and s.x_user_id in blocked_users) or (s.wallet and s.wallet.lower() in blocked_wallets):
            skipped.append((s.idea_id, f"creator already won within {cooldown_days} days"))
            continue
        return WinnerResult(s, None, skipped)
    return WinnerResult(None, "all_creators_in_cooldown", skipped)


# ---------------------------------------------------------------------------
# Vote eligibility (brief section 4)
# ---------------------------------------------------------------------------

def vote_eligibility(*, balance_epc: Optional[float], min_epc: float, first_tx_at: Optional[datetime], now: datetime,
                     min_age_days: int) -> Optional[str]:
    """None when the wallet may vote, else the reason. Unknown data never passes (fail closed)."""
    if balance_epc is None:
        return "balance_unavailable"
    if balance_epc < min_epc:
        return "balance_too_low"
    if first_tx_at is None:
        return "wallet_age_unknown"
    if now - first_tx_at < timedelta(days=min_age_days):
        return "wallet_too_new"
    return None


def counts_at_close(balance_at_close: Optional[float], min_epc: float) -> bool:
    """A vote only counts if the wallet still holds the minimum when the vote closes (flash-loan guard)."""
    return balance_at_close is not None and balance_at_close >= min_epc


def is_self_vote(voter_wallet: str, voter_x_user_id: Optional[str], idea_wallet: str, idea_x_user_id: Optional[str]) -> bool:
    """A creator cannot vote for their own idea: checked on the wallet and on the X user id."""
    if voter_wallet.lower() == idea_wallet.lower():
        return True
    return bool(voter_x_user_id and idea_x_user_id and voter_x_user_id == idea_x_user_id)
