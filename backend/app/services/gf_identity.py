"""
GoForge Registry identity (brief section 3.2 and 4). Wallet and X handle always come from a signature or from OAuth,
never from text the user typed.

- SIWE (EIP-4361): the wallet address is recovered from the signature and compared with the one in the message.
- Session: a signed token (HS256) that only carries the wallet. Without GF_SESSION_SECRET no session is ever issued.
- X OAuth 2.0 with PKCE: handle, verified flag, account age and followers come from the X API for the token's own user.
- Pairing: `wallet <-> x_user_id` (the id, not the handle, which can change). One X account, one active wallet.
- Votes: EIP-712 typed data, signed off-chain (gasless).
"""
import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import jwt
from eth_account import Account
from eth_utils import to_checksum_address
from eth_account.messages import encode_defunct, encode_typed_data

SIWE_STATEMENT = "Sign in to GoForge by Epoch Labs. This does not cost gas and does not move any funds."
NONCE_MAX_AGE = timedelta(minutes=10)
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_ME_URL = "https://api.x.com/2/users/me"
X_SCOPES = "users.read tweet.read"


class IdentityError(ValueError):
    """A login or signature problem. The message is safe to show the user."""


# ---------------------------------------------------------------------------
# SIWE (EIP-4361)
# ---------------------------------------------------------------------------

def new_nonce() -> str:
    return secrets.token_hex(16)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_siwe_message(*, domain: str, address: str, uri: str, chain_id: int, nonce: str, issued_at: datetime,
                       statement: str = SIWE_STATEMENT, expires_at: Optional[datetime] = None) -> str:
    """The exact EIP-4361 text the wallet signs. The frontend builds the same text; the backend re-parses it."""
    lines = [f"{domain} wants you to sign in with your Ethereum account:", address, "", statement, "",
             f"URI: {uri}", "Version: 1", f"Chain ID: {chain_id}", f"Nonce: {nonce}", f"Issued At: {_iso(issued_at)}"]
    if expires_at:
        lines.append(f"Expiration Time: {_iso(expires_at)}")
    return "\n".join(lines)


_SIWE_RE = re.compile(
    r"^(?P<domain>[^\n]+) wants you to sign in with your Ethereum account:\n(?P<address>0x[0-9a-fA-F]{40})\n\n"
    r"(?:(?P<statement>[^\n]*)\n\n)?URI: (?P<uri>[^\n]+)\nVersion: (?P<version>[^\n]+)\nChain ID: (?P<chain_id>\d+)\n"
    r"Nonce: (?P<nonce>[A-Za-z0-9]{8,})\nIssued At: (?P<issued_at>[^\n]+)(?:\nExpiration Time: (?P<expires>[^\n]+))?$")


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_siwe_message(message: str) -> dict:
    m = _SIWE_RE.match(message.replace("\r\n", "\n"))
    if not m:
        raise IdentityError("The sign-in message is not a valid EIP-4361 message.")
    d = m.groupdict()
    try:
        d["issued_at"] = _parse_time(d["issued_at"])
        d["expires"] = _parse_time(d["expires"]) if d["expires"] else None
    except ValueError:
        raise IdentityError("The sign-in message has an invalid time.")
    d["chain_id"] = int(d["chain_id"])
    return d


def verify_siwe(message: str, signature: str, *, expected_domain: str, expected_chain_id: int, now: datetime) -> dict:
    """
    Check an EIP-4361 login. The wallet is recovered from the signature, so it cannot be claimed by typing an address.
    Returns {address (lower case), nonce, issued_at}. The caller must still consume the nonce (single use).
    """
    parsed = parse_siwe_message(message)
    if parsed["domain"] != expected_domain:
        raise IdentityError("The sign-in message was made for a different site.")
    if parsed["version"] != "1":
        raise IdentityError("Unsupported sign-in message version.")
    if parsed["chain_id"] != expected_chain_id:
        raise IdentityError("The sign-in message is for a different chain.")
    if now - parsed["issued_at"] > NONCE_MAX_AGE or parsed["issued_at"] - now > timedelta(minutes=2):
        raise IdentityError("The sign-in message has expired. Try again.")
    if parsed["expires"] and now >= parsed["expires"]:
        raise IdentityError("The sign-in message has expired. Try again.")
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception:
        raise IdentityError("The signature is not valid.")
    if recovered.lower() != parsed["address"].lower():
        raise IdentityError("The signature does not match the wallet in the message.")
    return {"address": recovered.lower(), "nonce": parsed["nonce"], "issued_at": parsed["issued_at"]}


# ---------------------------------------------------------------------------
# Session token
# ---------------------------------------------------------------------------

def issue_session(secret: str, wallet: str, now: datetime, ttl_hours: int) -> str:
    if not secret:
        raise IdentityError("Login is not configured.")
    claims = {"sub": wallet.lower(), "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=ttl_hours)).timestamp()),
              "iss": "goforge"}
    return jwt.encode(claims, secret, algorithm="HS256")


def read_session(secret: str, token: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """The wallet of a valid session token, else None (missing secret, bad signature, expired, wrong issuer)."""
    if not secret or not token:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], issuer="goforge", options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    wallet = str(claims.get("sub", ""))
    return wallet.lower() if ADDRESS_RE.match(wallet) else None


# ---------------------------------------------------------------------------
# X OAuth 2.0 (PKCE)
# ---------------------------------------------------------------------------

def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def x_authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return X_AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": X_SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def parse_x_user(payload: dict) -> dict:
    """Normalise /2/users/me. Every field comes from X for the token's own user; a missing field stays None."""
    data = (payload or {}).get("data") or {}
    if not data.get("id"):
        raise IdentityError("X did not return an account.")
    created = data.get("created_at")
    try:
        created_at = _parse_time(created) if created else None
    except ValueError:
        created_at = None
    metrics = data.get("public_metrics") or {}
    return {"x_user_id": str(data["id"]), "x_handle": data.get("username"), "x_verified": data.get("verified"),
            "x_created_at": created_at, "x_followers": metrics.get("followers_count")}


async def x_exchange_code(client, *, client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str) -> str:
    """Authorization code -> access token (confidential client, HTTP Basic)."""
    auth = (client_id, client_secret) if client_secret else None
    res = await client.post(X_TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier,
        "client_id": client_id}, auth=auth, headers={"content-type": "application/x-www-form-urlencoded"})
    if res.status_code != 200:
        raise IdentityError("X rejected the login. Try again.")
    token = (res.json() or {}).get("access_token")
    if not token:
        raise IdentityError("X did not return an access token.")
    return token


async def x_fetch_user(client, access_token: str) -> dict:
    res = await client.get(X_ME_URL, params={"user.fields": "verified,created_at,public_metrics"},
                           headers={"authorization": f"Bearer {access_token}"})
    if res.status_code != 200:
        raise IdentityError("Could not read your X account.")
    return parse_x_user(res.json())


# ---------------------------------------------------------------------------
# wallet <-> X pairing
# ---------------------------------------------------------------------------

def link_decision(by_x: Optional[dict], by_wallet: Optional[dict], x_user_id: str, wallet: str) -> tuple[str, Optional[str]]:
    """
    ('link' | 'refresh' | 'refuse', reason). by_x / by_wallet are the existing gf_creators rows for that X user id /
    wallet. One X account is tied to one active wallet; changing it is a manual, logged process, never self-service.
    """
    wallet = wallet.lower()
    if by_x is not None and by_x.get("wallet") and by_x["wallet"].lower() != wallet:
        return "refuse", "This X account is already linked to a different wallet. Contact the team to change it."
    if by_wallet is not None and by_wallet.get("x_user_id") != x_user_id:
        return "refuse", "This wallet is already linked to a different X account."
    if by_x is not None and by_x.get("wallet") and by_x["wallet"].lower() == wallet:
        return "refresh", None
    return "link", None


def is_team_identity(wallet: Optional[str], x_user_id: Optional[str], team_wallets: set[str], team_x_ids: set[str]) -> bool:
    """The team (and Golem's own wallets) may not submit or vote. Checked on both identities."""
    return bool((wallet and wallet.lower() in team_wallets) or (x_user_id and x_user_id in team_x_ids))


# ---------------------------------------------------------------------------
# EIP-712 vote
# ---------------------------------------------------------------------------

VOTE_DOMAIN_NAME = "EpochLabs GoForge"
VOTE_DOMAIN_VERSION = "1"


def vote_typed_data(*, round_date: str, idea_id: str, voter: str, signed_at: int, chain_id: int) -> dict:
    """
    The EIP-712 message a voter signs. `signedAt` (unix seconds) makes every signature unique and lets the backend
    refuse an older signature replayed after the voter changed their vote.
    """
    return {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                             {"name": "chainId", "type": "uint256"}],
            "Vote": [{"name": "round", "type": "string"}, {"name": "ideaId", "type": "string"},
                     {"name": "voter", "type": "address"}, {"name": "signedAt", "type": "uint256"}],
        },
        "primaryType": "Vote",
        "domain": {"name": VOTE_DOMAIN_NAME, "version": VOTE_DOMAIN_VERSION, "chainId": chain_id},
        "message": {"round": round_date, "ideaId": idea_id, "voter": voter, "signedAt": signed_at},
    }


def recover_vote_signer(typed: dict, signature: str) -> str:
    try:
        recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=signature)
    except Exception:
        raise IdentityError("The vote signature is not valid.")
    return recovered.lower()


def verify_vote(*, round_date: str, idea_id: str, voter: str, signed_at: int, chain_id: int, signature: str,
                now: datetime, max_skew_s: int = 600) -> str:
    """The signature must come from `voter` over exactly this round, idea and time. Returns the voter (lower case)."""
    if abs(int(now.timestamp()) - int(signed_at)) > max_skew_s:
        raise IdentityError("The vote signature is too old. Sign again.")
    typed = vote_typed_data(round_date=round_date, idea_id=idea_id, voter=voter, signed_at=signed_at, chain_id=chain_id)
    if recover_vote_signer(typed, signature) != voter.lower():
        raise IdentityError("The vote signature does not match your wallet.")
    return voter.lower()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
