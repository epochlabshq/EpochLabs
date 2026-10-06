"""
What the Radar worker is doing, kept in process memory and served by GET /api/radar/status, so a missing run
can be diagnosed from outside instead of guessed from logs. Holds a short state word and a one-line reason,
never a stack trace.
"""
import importlib.util
from datetime import datetime, timezone

_status: dict = {"state": "starting", "detail": None, "at": None}


def set_status(state: str, detail: str | None = None) -> None:
    _status.update(state=state, detail=(detail or "")[:200] or None, at=datetime.now(timezone.utc).isoformat())
    print(f"[RADAR WORKER] {state}" + (f": {detail}" if detail else ""), flush=True)


def embedders_installed() -> dict[str, bool]:
    """Whether each embedding backend can be imported (found without importing it: torch is heavy)."""
    return {name: importlib.util.find_spec(name) is not None for name in ("sentence_transformers", "fastembed")}


def snapshot() -> dict:
    return {**_status, "embedders": embedders_installed()}
