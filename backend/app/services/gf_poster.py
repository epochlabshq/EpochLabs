"""
GoForge Registry X posts (brief section 7): the winner at 20:05 UTC, the live post with the CA, and the verdict at hour 48.
Pure rendering; sending goes through the same twitter service as the Desk and is a dry run unless explicitly enabled.

No "$" before any ticker (X turns it into a stock card), no call to buy: only links to the scoreboard, Pons and Blockscout.

The brief's winner template is about 310 weighted characters, more than the 280 a standard X account can post. With
GF_X_MAX_CHARS=280 (default) the post is rendered in the first of four tiers that fits: the full template, a compact
wording, then without the ideas/votes line. An account with X Premium can set a higher limit and gets the full template.
"""
import re
from datetime import date
from typing import Callable, Optional

from app.core.config import settings
from app.services.desk_poster import strip_cashtags

SCOREBOARD_URL = "epochlabs.run/goforge"
_URL = re.compile(r"https?://\S+|epochlabs\.run/\S*")
# X counts most Latin text as 1 and everything else (emoji, many symbols, the ellipsis) as 2
_WEIGHT1 = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def x_weight(text: str) -> int:
    """Weighted length as X counts it: a URL is 23, characters outside the Latin ranges are 2."""
    text = _URL.sub("x" * 23, text)
    return sum(1 if any(lo <= ord(c) <= hi for lo, hi in _WEIGHT1) else 2 for c in text)


def max_chars() -> int:
    return settings.GF_X_MAX_CHARS


def _fit(tiers: list[Callable[[str], str]], name: str) -> str:
    """The first tier that fits with the full name; failing that the last tier with the name shortened (never the rest)."""
    limit = max_chars()
    for build in tiers:
        text = strip_cashtags(build(name))
        if x_weight(text) <= limit:
            return text
    last, base, n = tiers[-1], name, name
    while len(base) > 6:
        base = base[:-4].rstrip() if len(base) > 10 else base[:6]
        n = base + "…"
        text = strip_cashtags(last(n))
        if x_weight(text) <= limit:
            return text
    raise ValueError("post does not fit on X")


def render_winner_post(*, round_date: date, name: str, ticker: str, handle: str, final: float, credibility: float,
                       golem: float, vote: float, n_ideas: int, n_votes: int) -> str:
    h = handle.lstrip("@")
    head = f"⚒️ GoForge winner, {round_date.isoformat()}"
    score = f"Score: {final:.1f} · Credibility {credibility:.1f} · Golem {golem:.1f} · Votes {vote:.1f}"
    counts = f"{n_ideas} ideas submitted · {n_votes} votes cast"
    link = f"Full scoreboard: {SCOREBOARD_URL}"

    def full(n: str) -> str:
        return "\n".join([head, "", f"{n} ({ticker})", f"by @{h}", "", score, counts, "", "Golem launches it on Pons within 24h.",
                          "Creator fees: 50% to the creator, 50% to EPC buyback & burn.", "", link])

    def compact(n: str) -> str:
        return "\n".join([head, "", f"{n} ({ticker}) by @{h}", "", score, counts, "", "Launch on Pons within 24h.",
                          "Fees: 50% creator, 50% EPC buyback & burn.", link])

    def short(n: str) -> str:
        return "\n".join([head, "", f"{n} ({ticker}) by @{h}", score, "Launch on Pons within 24h.",
                          "Fees: 50% creator, 50% EPC buyback & burn.", link])

    def minimal(n: str) -> str:
        return "\n".join([head, f"{n} ({ticker}) by @{h}", score, "Fees: 50% creator, 50% EPC burn.", link])

    return _fit([full, compact, short, minimal], name)


def render_no_launch_post(*, round_date: date, reason_text: str, n_ideas: int, n_votes: int) -> str:
    text = strip_cashtags("\n".join([
        f"GoForge, {round_date.isoformat()}: no launch today.",
        "",
        reason_text,
        f"{n_ideas} ideas submitted · {n_votes} votes cast",
        "",
        f"Scoreboard: {SCOREBOARD_URL}",
    ]))
    if x_weight(text) > max_chars():
        raise ValueError("post does not fit on X")
    return text


def render_live_post(*, name: str, ticker: str, ca: str, pons_url: Optional[str], blockscout_url: str) -> str:
    def build(n: str) -> str:
        lines = [f"{n} ({ticker}) is live.", "", f"CA: {ca}"]
        if pons_url:
            lines.append(f"Pons: {pons_url}")
        lines += [f"Blockscout: {blockscout_url}", f"Scoreboard: {SCOREBOARD_URL}"]
        return "\n".join(lines)
    return _fit([build], name)


def render_verdict_post(*, name: str, ticker: str, verdict: str, peak_mc_usd: Optional[float],
                        fees_to_creator_eth: float, epc_burned: float) -> str:
    label = "reached the 30K market cap" if verdict == "reached_30k" else "stalled below the 30K market cap"
    peak = "n/a" if peak_mc_usd is None else f"${peak_mc_usd:,.0f}"

    def build(n: str) -> str:
        return "\n".join([
            f"GoForge verdict, 48h: {n} ({ticker}) {label}.",
            "",
            f"Peak market cap: {peak}",
            f"Creator fees paid out: {fees_to_creator_eth:.4f} ETH",
            f"EPC bought back and burned: {epc_burned:,.0f}",
            "",
            f"Scoreboard: {SCOREBOARD_URL}",
        ])
    return _fit([build], name)


NO_LAUNCH_TEXT = {
    "no_approved_ideas": "No idea passed moderation today.",
    "no_votes": "No eligible vote was cast today.",
    "all_creators_in_cooldown": "Every ranked creator had already won within the last 7 days.",
}
