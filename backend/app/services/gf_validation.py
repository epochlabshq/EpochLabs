"""
GoForge Registry: form validation and automatic moderation. Pure functions (no I/O): the caller passes in what it
looked up (existing tickers, other ideas of the day, the embedding), so every rule is unit-tested.

Automatic moderation can only reject or pass an idea to the manual review (brief 3.4). It never approves: nothing reaches
the vote list without a person looking at it. The checks that need a model the repo does not have (NSFW, brand logos in an
image) are therefore not faked: the image gets a `needs_visual_check` flag for the reviewer, and an `ImageModerator` hook
lets a real classifier plug in later.
"""
import hashlib
import io
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Sequence

from app.services.lore_safety import URL_RE, check_profanity, strip_zero_width_and_bidi

NAME_MIN, NAME_MAX = 2, 32
TICKER_MIN, TICKER_MAX = 2, 10
LORE_MIN, LORE_MAX = 50, 1000
IMAGE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MIN_SIDE = 512
IMAGE_MAX_SIDE = 4096  # guards against decompression bombs
IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
MIN_LORE_WORDS = 8
MIN_UNIQUE_WORD_RATIO = 0.35

TICKER_RE = re.compile(r"^[A-Z0-9]+$")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class Problem:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# Text fields
# ---------------------------------------------------------------------------

def clean_text(s: Optional[str]) -> str:
    """NFKC, zero-width and bidi characters removed, control characters dropped, whitespace collapsed."""
    s = unicodedata.normalize("NFKC", strip_zero_width_and_bidi(s or ""))
    s = "".join(ch for ch in s if ch in "\n\t " or not unicodedata.category(ch).startswith("C"))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", s)).strip()


def validate_fields(name: Optional[str], ticker: Optional[str], lore: Optional[str]) -> tuple[Optional[dict], list[Problem]]:
    """(clean fields, []) or (None, problems). The ticker is upper-cased; a `$` is refused, not silently dropped."""
    problems: list[Problem] = []
    n = clean_text(name).replace("\n", " ")
    t = (ticker or "").strip().upper()
    lo = clean_text(lore)

    if not NAME_MIN <= len(n) <= NAME_MAX:
        problems.append(Problem("name", "length", f"Name must be {NAME_MIN}-{NAME_MAX} characters."))
    elif URL_RE.search(n):
        problems.append(Problem("name", "link", "The name cannot contain a link."))
    if not TICKER_MIN <= len(t) <= TICKER_MAX:
        problems.append(Problem("ticker", "length", f"Ticker must be {TICKER_MIN}-{TICKER_MAX} characters."))
    elif not TICKER_RE.match(t):
        problems.append(Problem("ticker", "charset", "Ticker can only use letters and numbers (no $ or symbols)."))
    if not LORE_MIN <= len(lo) <= LORE_MAX:
        problems.append(Problem("lore", "length", f"Lore must be {LORE_MIN}-{LORE_MAX:,} characters."))
    else:
        problems.extend(_lore_safety(lo))
    if problems:
        return None, problems
    return {"name": n, "ticker": t, "lore": lo}, []


def _lore_safety(lore: str) -> list[Problem]:
    """The existing Lore Safety rules (profanity, links) plus a spam check. Any hit is a rejection."""
    if URL_RE.search(lore):
        return [Problem("lore", "link", "Lore cannot contain links.")]
    if check_profanity(lore):
        return [Problem("lore", "unsafe", "Lore contains language that is not allowed.")]
    words = re.findall(r"\w+", lore.lower())
    if len(words) < MIN_LORE_WORDS:
        return [Problem("lore", "too_few_words", f"Lore needs at least {MIN_LORE_WORDS} words.")]
    if len(set(words)) / len(words) < MIN_UNIQUE_WORD_RATIO:
        return [Problem("lore", "spam", "Lore looks repetitive. Write it out in your own words.")]
    return []


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageInfo:
    sha256: str
    ext: str
    width: int
    height: int
    size: int


def validate_image(data: bytes) -> tuple[Optional[ImageInfo], list[Problem]]:
    """Format, size, minimum resolution and exactly 1:1. The file is decoded, so a renamed or truncated file fails."""
    if not data:
        return None, [Problem("image", "missing", "An image is required.")]
    if len(data) > IMAGE_MAX_BYTES:
        return None, [Problem("image", "too_large", "Image must be 2 MB or smaller.")]
    from PIL import Image, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(data))
        fmt, (w, h) = img.format, img.size
        if fmt not in IMAGE_FORMATS:
            return None, [Problem("image", "format", "Image must be PNG, JPG or WebP.")]
        if w > IMAGE_MAX_SIDE or h > IMAGE_MAX_SIDE:
            return None, [Problem("image", "too_big_dimensions", f"Image cannot exceed {IMAGE_MAX_SIDE}px on a side.")]
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, [Problem("image", "unreadable", "The image could not be read. Upload a valid PNG, JPG or WebP.")]
    if w != h:
        return None, [Problem("image", "ratio", "Image must be square (1:1).")]
    if w < IMAGE_MIN_SIDE:
        return None, [Problem("image", "too_small", f"Image must be at least {IMAGE_MIN_SIDE}x{IMAGE_MIN_SIDE}px.")]
    return ImageInfo(hashlib.sha256(data).hexdigest(), IMAGE_FORMATS[fmt], w, h, len(data)), []


class ImageModerator(Protocol):
    """Plug-in point for a real NSFW / logo classifier. Returns a list of reasons to reject, empty when clean."""
    def __call__(self, data: bytes) -> list[str]: ...


# ---------------------------------------------------------------------------
# Brand / person names and tickers
# ---------------------------------------------------------------------------

def normalize_for_match(s: str) -> str:
    """Lower case, leetspeak undone, everything but letters removed: '3L0N' and 'E-L-O-N' both become 'elon'."""
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKC", s).lower().translate(_LEET))


def _tokens(s: str) -> list[str]:
    return [normalize_for_match(w) for w in re.split(r"[^A-Za-z0-9@$!]+", s) if w]


@dataclass(frozen=True)
class BlockLists:
    names: frozenset[str]
    tickers: frozenset[str]


_blocklists_cache: Optional[BlockLists] = None


def _split_env(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def load_blocklists(config_dir: Optional[Path] = None, extra_names: str = "", extra_tickers: str = "") -> BlockLists:
    d = Path(config_dir) if config_dir else CONFIG_DIR
    names: set[str] = set()
    tickers: set[str] = set()
    p = d / "gf_blocked_names.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        for key in ("companies", "people"):
            names.update(normalize_for_match(x) for x in raw.get(key, []))
    p = d / "gf_stock_tickers.json"
    if p.exists():
        tickers.update(t.strip().upper() for t in json.loads(p.read_text(encoding="utf-8")).get("tickers", []))
    names.update(normalize_for_match(x) for x in _split_env(extra_names))
    tickers.update(t.upper() for t in _split_env(extra_tickers))
    names.discard("")
    return BlockLists(frozenset(names), frozenset(tickers))


def get_blocklists() -> BlockLists:
    global _blocklists_cache
    if _blocklists_cache is None:
        from app.core.config import settings
        _blocklists_cache = load_blocklists(extra_names=settings.GF_EXTRA_BLOCKED_NAMES,
                                            extra_tickers=settings.GF_EXTRA_STOCK_TICKERS)
    return _blocklists_cache


def _matches_term(token: str, term: str) -> bool:
    """A short term (under 4 letters) must be the whole token; a longer one also matches as a prefix or suffix
    ('elondoge', 'teslaverse'), which is how brand names are usually dressed up."""
    if token == term:
        return True
    return len(term) >= 4 and len(token) > len(term) and (token.startswith(term) or token.endswith(term))


def find_blocked_name(name: str, ticker: str, names: frozenset[str]) -> Optional[str]:
    """The blocked term used by the name or the ticker, or None."""
    toks = _tokens(name) + [normalize_for_match(ticker)]
    joined = "".join(_tokens(name))
    for term in names:
        if any(_matches_term(t, term) for t in toks) or _matches_term(joined, term):
            return term
        # the whole name run together ('Nvidia Coin' -> 'nvidiacoin'), for terms long enough to be unambiguous
        if len(term) >= 5 and term in joined:
            return term
    return None


def find_ticker_collision(ticker: str, stock_tickers: frozenset[str], existing: Iterable[str]) -> Optional[str]:
    """'stock' when it equals a large listed ticker, 'existing' when a token already uses it, else None."""
    t = ticker.upper()
    if t in stock_tickers:
        return "stock"
    if t in {e.upper() for e in existing if e}:
        return "existing"
    return None


# ---------------------------------------------------------------------------
# Duplicates (same day)
# ---------------------------------------------------------------------------

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def hash_embedding(text: str, dim: int = 256) -> list[float]:
    """Deterministic fallback when no embedding model is installed: hashed character 3-grams, L2-normalised. It catches
    near-copies of the same text; it does not understand meaning, so the model embedding is used whenever it exists."""
    t = f"  {re.sub(r'[^a-z0-9 ]', '', clean_text(text).lower())}  "
    v = [0.0] * dim
    for i in range(len(t) - 2):
        h = int.from_bytes(hashlib.blake2b(t[i:i + 3].encode(), digest_size=4).digest(), "big")
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@dataclass(frozen=True)
class OtherIdea:
    idea_id: str
    name: str
    ticker: str
    embedding: Optional[Sequence[float]]


def find_duplicate(name: str, ticker: str, embedding: Optional[Sequence[float]], others: Iterable[OtherIdea],
                   threshold: float) -> Optional[tuple[str, str]]:
    """(idea_id, why) of an earlier idea of the same round that this one nearly copies, or None."""
    n_key, t_key = normalize_for_match(name), ticker.upper()
    for o in others:
        if t_key == o.ticker.upper():
            return o.idea_id, "ticker"
        if n_key and n_key == normalize_for_match(o.name):
            return o.idea_id, "name"
        if embedding is not None and o.embedding is not None and cosine(embedding, o.embedding) >= threshold:
            return o.idea_id, "lore"
    return None


# ---------------------------------------------------------------------------
# The automatic stage
# ---------------------------------------------------------------------------

REJECT_MESSAGES = {
    "brand_or_person": "The name or ticker uses a brand, company or real person's name.",
    "ticker_stock": "That ticker collides with a listed stock.",
    "ticker_existing": "That ticker is already used by a token on Robinhood Chain.",
    "duplicate_ticker": "Another idea today already uses that ticker.",
    "duplicate_name": "Another idea today has the same name.",
    "duplicate_lore": "Another idea today has almost the same lore.",
    "image_rejected": "The image was rejected by the automatic image check.",
}


@dataclass
class ModerationResult:
    status: str                       # 'pending_review' (to the manual review) or 'rejected'
    reason: Optional[str] = None      # text the submitter sees when rejected
    flags: dict = field(default_factory=dict)


def auto_moderate(*, name: str, ticker: str, embedding: Optional[Sequence[float]], others: Iterable[OtherIdea],
                  existing_symbols: Iterable[str], blocklists: BlockLists, threshold: float,
                  image_bytes: Optional[bytes] = None, image_moderator: Optional[ImageModerator] = None,
                  embedder_label: str = "unknown") -> ModerationResult:
    """First failing check wins and is the reason shown. Otherwise the idea waits for the team (`pending_review`)."""
    term = find_blocked_name(name, ticker, blocklists.names)
    if term:
        return ModerationResult("rejected", REJECT_MESSAGES["brand_or_person"], {"blocked_term": term})
    collision = find_ticker_collision(ticker, blocklists.tickers, existing_symbols)
    if collision:
        return ModerationResult("rejected", REJECT_MESSAGES[f"ticker_{collision}"], {"ticker_collision": collision})
    dup = find_duplicate(name, ticker, embedding, others, threshold)
    if dup:
        return ModerationResult("rejected", REJECT_MESSAGES[f"duplicate_{dup[1]}"],
                                {"duplicate_of": dup[0], "duplicate_by": dup[1]})
    if image_moderator is not None and image_bytes is not None:
        reasons = image_moderator(image_bytes)
        if reasons:
            return ModerationResult("rejected", REJECT_MESSAGES["image_rejected"], {"image_reasons": reasons})
    flags: dict = {"embedder": embedder_label}
    # No classifier is installed, so a person has to look at the picture (NSFW, brand logos): said openly, not skipped
    flags["needs_visual_check"] = image_moderator is None
    return ModerationResult("pending_review", None, flags)
