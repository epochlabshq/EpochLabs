"""
GoForge Registry round schedule. Pure functions (no I/O): every boundary comes from a Schedule built from settings,
nothing is hardcoded here.

One round per UTC day, keyed by the date submissions open:

    submit  [open, close)          ideas can be submitted
    vote    [close, vote_close)    approved ideas are voted on
    scoring [vote_close, +minutes) votes are closed, balances re-checked, scores computed
    announced  from vote_close + minutes until the next round opens (the winner is public)

The DB enum also has `review`, `launched` and `no_launch`: `review` is not a time window (manual review runs through
submit and the vote, an idea approved late still joins the pool), `launched` / `no_launch` are outcomes set by the worker.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

PHASES = ("submit", "vote", "scoring", "announced")


@dataclass(frozen=True)
class Schedule:
    open_hour: int = 0
    close_hour: int = 12
    vote_close_hour: int = 20
    announce_minute: int = 5
    launch_window_hours: int = 24

    def __post_init__(self):
        if not 0 <= self.open_hour < self.close_hour < self.vote_close_hour <= 23:
            raise ValueError("schedule must satisfy open < submit close < vote close, all within one UTC day")
        if not 0 <= self.announce_minute < 60:
            raise ValueError("announce_minute must be within 0-59")

    @classmethod
    def from_settings(cls, s) -> "Schedule":
        return cls(s.GF_SUBMIT_OPEN_HOUR_UTC, s.GF_SUBMIT_CLOSE_HOUR_UTC, s.GF_VOTE_CLOSE_HOUR_UTC,
                   s.GF_ANNOUNCE_MINUTE_UTC, s.GF_LAUNCH_WINDOW_HOURS)


def _at(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=timezone.utc)


def round_date_at(now: datetime, sch: Schedule) -> date:
    """The round a moment belongs to: the UTC date, or the previous one before the day's submit opens."""
    now = now.astimezone(timezone.utc)
    return now.date() if now >= _at(now.date(), sch.open_hour) else now.date() - timedelta(days=1)


def boundaries(round_date: date, sch: Schedule) -> dict[str, datetime]:
    return {
        "opens": _at(round_date, sch.open_hour),
        "submit_closes": _at(round_date, sch.close_hour),
        "vote_closes": _at(round_date, sch.vote_close_hour),
        "announces": _at(round_date, sch.vote_close_hour, sch.announce_minute),
        "next_opens": _at(round_date + timedelta(days=1), sch.open_hour),
    }


def phase_at(now: datetime, sch: Schedule) -> str:
    """submit | vote | scoring | announced for the round `now` belongs to."""
    now = now.astimezone(timezone.utc)
    b = boundaries(round_date_at(now, sch), sch)
    if now < b["submit_closes"]:
        return "submit"
    if now < b["vote_closes"]:
        return "vote"
    if now < b["announces"]:
        return "scoring"
    return "announced"


def next_boundary(now: datetime, sch: Schedule) -> tuple[str, datetime]:
    """(label, when) of the next phase change: what the countdown in the hero counts down to."""
    now = now.astimezone(timezone.utc)
    b = boundaries(round_date_at(now, sch), sch)
    for label, key in (("submit_closes", "submit_closes"), ("vote_closes", "vote_closes"),
                       ("announcement", "announces"), ("submit_opens", "next_opens")):
        if now < b[key]:
            return label, b[key]
    return "submit_opens", b["next_opens"]


def submit_open(now: datetime, sch: Schedule) -> bool:
    return phase_at(now, sch) == "submit"


def vote_open(now: datetime, sch: Schedule) -> bool:
    return phase_at(now, sch) == "vote"


def scoreboard_visible(now: datetime, round_date: date, sch: Schedule) -> bool:
    """The full score table is published from the moment the vote closes (20:00 UTC), never before."""
    return now.astimezone(timezone.utc) >= boundaries(round_date, sch)["vote_closes"]


def votes_visible(now: datetime, round_date: date, sch: Schedule) -> bool:
    """Every vote (wallet + signature) is published once the vote phase is closed, so the result can be audited."""
    return scoreboard_visible(now, round_date, sch)


def launch_deadline(announced_at: datetime, sch: Schedule) -> datetime:
    return announced_at + timedelta(hours=sch.launch_window_hours)
