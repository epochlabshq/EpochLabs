"""
GoForge launch registry: `backend/config/goforge.launches.json`, one entry per token Golem launched.

Kept free of imports from app.core.config so Settings can read it (the trading exclude list is built from it).
Entries are append-only by policy: a failed launch stays in the file forever.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "goforge.launches.json"

WHY_FIELDS = ("window", "window_reason", "lore_summary", "model_run_id", "why_hash")
RULE_FIELDS = ("hook_address", "lp_lock_address", "lp_lock_until", "anti_snipe_blocks",
               "wallet_cap_pct", "wallet_cap_minutes", "min_liquidity_usd")


class GoForgeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LaunchEntry:
    id: str
    ca: str                      # lower-cased
    launch_tx: str               # lower-cased
    why: dict
    rules: dict
    fee_router_address: Optional[str]


def config_path() -> Path:
    return Path(os.getenv("GOFORGE_LAUNCHES_PATH") or DEFAULT_PATH)


def _address(value, field: str, entry_id: str, required: bool = False) -> Optional[str]:
    if value in (None, ""):
        if required:
            raise GoForgeConfigError(f"{entry_id}: {field} is required")
        return None
    if not isinstance(value, str) or not ADDRESS_RE.match(value):
        raise GoForgeConfigError(f"{entry_id}: {field} is not a valid address")
    return value.lower()


def parse_entries(raw) -> list[LaunchEntry]:
    """Validate the decoded JSON. Raises GoForgeConfigError on the first problem (a bad file must be loud)."""
    if not isinstance(raw, list):
        raise GoForgeConfigError("goforge.launches.json must be a JSON array")
    entries: list[LaunchEntry] = []
    seen_ids: set[str] = set()
    seen_cas: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GoForgeConfigError(f"entry {i} is not an object")
        entry_id = item.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise GoForgeConfigError(f"entry {i}: id is required")
        ca = _address(item.get("ca"), "ca", entry_id, required=True)
        tx = item.get("launch_tx")
        if not isinstance(tx, str) or not TX_RE.match(tx):
            raise GoForgeConfigError(f"{entry_id}: launch_tx must be a 32-byte tx hash")
        if entry_id in seen_ids:
            raise GoForgeConfigError(f"duplicate id {entry_id}")
        if ca in seen_cas:
            raise GoForgeConfigError(f"duplicate ca {ca}")
        seen_ids.add(entry_id)
        seen_cas.add(ca)

        why_in = item.get("why") or {}
        rules_in = item.get("rules") or {}
        why = {k: why_in.get(k) for k in WHY_FIELDS}
        rules = {k: rules_in.get(k) for k in RULE_FIELDS}
        for k in ("hook_address", "lp_lock_address"):
            rules[k] = _address(rules[k], f"rules.{k}", entry_id)
        entries.append(LaunchEntry(
            id=entry_id, ca=ca, launch_tx=tx.lower(), why=why, rules=rules,
            fee_router_address=_address(item.get("fee_router_address"), "fee_router_address", entry_id),
        ))
    return entries


_cache: dict = {"path": None, "mtime": None, "entries": []}


def load_launches(path: Optional[Path] = None) -> list[LaunchEntry]:
    """Entries from the config file, re-read only when the file changes. A missing file means no launches."""
    p = Path(path) if path else config_path()
    try:
        mtime = p.stat().st_mtime_ns
    except FileNotFoundError:
        return []
    if _cache["path"] == str(p) and _cache["mtime"] == mtime:
        return _cache["entries"]
    entries = parse_entries(json.loads(p.read_text(encoding="utf-8")))
    _cache.update(path=str(p), mtime=mtime, entries=entries)
    return entries


def goforge_cas() -> frozenset[str]:
    """Every GoForge CA, lower-cased. Golem must never buy or sell any of them.

    Never raises: a broken config file must not take trading down, but it must not silently shrink the exclude
    list either, so the last good copy is kept and the error is logged.
    """
    try:
        return frozenset(e.ca for e in load_launches())
    except (GoForgeConfigError, ValueError, OSError) as e:
        print(f"[GOFORGE] Config unreadable, using last good copy: {e}", flush=True)
        return frozenset(e.ca for e in _cache["entries"])
