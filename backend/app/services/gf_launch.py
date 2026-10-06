"""
GoForge Registry launch planning (brief section 6). Pure functions plus the launch backend interface.

Golem chooses the launch hour; it does not launch by itself unless a backend that can reach Pons exists. Whether Pons can
be driven by the agent is an open question (brief 13), so the default backend is `manual`: Golem schedules the launch and
the team launches on Pons, then registers the CA through the admin endpoint. A `pons` backend is a stub that refuses
instead of pretending.
"""
import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Protocol

from app.services.desk import canonical_json

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
MIN_LEAD_MINUTES = 30


def _ceil_hour(t: datetime) -> datetime:
    t = t.astimezone(timezone.utc)
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if floor == t else floor + timedelta(hours=1)


def _rate(hour_rates: Optional[Mapping], hour: int) -> Optional[float]:
    if not hour_rates:
        return None
    v = hour_rates.get(str(hour), hour_rates.get(hour))
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def pick_launch_time(announced_at: datetime, hour_rates: Optional[Mapping], window_hours: int,
                     min_lead_minutes: int = MIN_LEAD_MINUTES) -> tuple[datetime, str]:
    """
    The launch slot: the whole UTC hour with the highest survival rate (model `hour_rates`) between
    `announced_at + lead` and `announced_at + window`. Ties go to the earlier hour. Without any hour data the first slot
    after the announcement is used and the reason says so.
    """
    start = _ceil_hour(announced_at + timedelta(minutes=min_lead_minutes))
    end = announced_at + timedelta(hours=window_hours)
    slots: list[datetime] = []
    t = start
    while t <= end:
        slots.append(t)
        t += timedelta(hours=1)
    if not slots:
        return start, "No full hour fits the launch window: first slot after the announcement."
    rated = [(s, _rate(hour_rates, s.hour)) for s in slots]
    known = [(s, r) for s, r in rated if r is not None]
    if not known:
        return slots[0], "No survival data by hour yet: first slot after the announcement."
    best_slot, best_rate = max(known, key=lambda x: (x[1], -x[0].timestamp()))
    return best_slot, (f"{best_slot.hour:02d}:00 UTC has the highest survival rate in the model data "
                       f"({best_rate:.0%}) inside the 24 hour window.")


def scoreboard_rows_canonical(rows: list[dict]) -> str:
    """Canonical JSON of the published scoreboard: the same bytes for anyone who rebuilds it."""
    keep = ("rank", "idea_id", "name", "ticker", "x_handle", "credibility", "golem", "vote", "final", "votes")
    return canonical_json({"rows": [{k: r.get(k) for k in keep} for r in rows]})


def scoreboard_hash(rows: list[dict]) -> str:
    return hashlib.sha256(scoreboard_rows_canonical(rows).encode("utf-8")).hexdigest()


class RegistrationError(ValueError):
    pass


def validate_registration(ca: str, launch_tx: str, splitter: Optional[str]) -> dict:
    """Formats of what the team registers after launching on Pons. The CA and tx are stored lower-cased."""
    if not ADDRESS_RE.match(ca or ""):
        raise RegistrationError("ca is not a valid address")
    if not TX_RE.match(launch_tx or ""):
        raise RegistrationError("launch_tx is not a valid tx hash")
    if splitter and not ADDRESS_RE.match(splitter):
        raise RegistrationError("splitter_address is not a valid address")
    return {"ca": ca.lower(), "launch_tx": launch_tx.lower(), "splitter_address": splitter.lower() if splitter else None}


class LaunchBackendError(RuntimeError):
    pass


class LaunchBackend(Protocol):
    async def launch(self, idea: dict) -> Optional[dict]:
        """Launch the idea. Returns {ca, launch_tx, splitter_address} when it launched, None when it is left to a person."""
        ...


class ManualLaunchBackend:
    """Default: Golem picked the slot, the team launches on Pons and registers the CA (admin endpoint)."""
    async def launch(self, idea: dict) -> Optional[dict]:
        return None


class PonsLaunchBackend:
    """Placeholder until the Pons launch API is known. It refuses, so nothing is ever 'launched' on paper."""
    async def launch(self, idea: dict) -> Optional[dict]:
        raise LaunchBackendError("The Pons launch API is not integrated. Use GF_LAUNCH_MODE=manual.")


def get_backend(mode: str) -> LaunchBackend:
    if mode == "pons":
        return PonsLaunchBackend()
    return ManualLaunchBackend()
