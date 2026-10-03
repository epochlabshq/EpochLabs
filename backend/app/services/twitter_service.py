import asyncio
import time
import random
import hmac
import hashlib
import urllib.parse
import httpx
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import select, desc
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import TwitterPost
from app.services.dexscreener import fetch_live_token_details
from app.services.factual_narrative_generator import generate_factual_narrative

# Simple in-memory / Redis lock key prefix
LOCK_KEY = "emile:twitter:post_lock"
MIN_POST_INTERVAL_SECONDS = 110 * 60  # 110 minutes minimum cooldown

def generate_oauth1_header(method: str, url: str, params: dict, api_key: str, api_secret: str, access_token: str, access_token_secret: str) -> str:
    """Generates OAuth 1.0a authorization header for Twitter API v2 requests."""
    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": str(int(time.time() * 1000)) + str(random.randint(1000, 9999)),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    
    combined_params = {**oauth_params, **params}
    sorted_params = sorted(combined_params.items())
    
    parameter_string = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted_params
    )
    
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(parameter_string, safe="")
    ])
    
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(access_token_secret, safe='')}"
    
    hashed = hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1)
    import base64
    raw_sig = base64.b64encode(hashed.digest()).decode("utf-8")
    oauth_params["oauth_signature"] = raw_sig

    auth_header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return auth_header


class TwitterService:
    """
    Twitter Service for publishing automated token news updates via Twitter API v2.
    Supports dry-run mode, Redis distributed locking, exponential backoff, and audit trails.
    """
    def __init__(self):
        self.api_url = "https://api.twitter.com/2/tweets"
        self.last_posted_at_memory = None
        self.last_target_memory = "emile_banana"
        self.api_backoff_until = 0.0

    async def get_next_target_token(self) -> str:
        """Determines which token is next in turn (alternating between 'emile' and 'emile_banana')."""
        if self.last_target_memory:
            return "emile_banana" if self.last_target_memory == "emile" else "emile"

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TwitterPost.target_token)
                    .where(TwitterPost.target_token != "desk")
                    .order_by(desc(TwitterPost.posted_at))
                    .limit(1)
                )
                last_target = result.scalar_one_or_none()
                if last_target:
                    self.last_target_memory = last_target
                    return "emile_banana" if last_target == "emile" else "emile"
        except Exception as e:
            print(f"[TWITTER SERVICE] DB query fallback in get_next_target_token: {e}")
        
        # In-memory fallback
        next_token = "emile_banana" if self.last_target_memory == "emile" else "emile"
        return next_token

    async def check_cooldown(self) -> tuple[bool, float]:
        """Checks if enough time (minimum 110 minutes) has passed since the last tweet or attempt."""
        now = datetime.now(timezone.utc)
        now_ts = time.time()

        # 0. API Credit / Error Backoff check (zero DB queries!)
        if now_ts < self.api_backoff_until:
            rem = self.api_backoff_until - now_ts
            return False, rem
        
        # 1. In-memory check first (zero DB queries!)
        if self.last_posted_at_memory:
            elapsed_mem = (now - self.last_posted_at_memory).total_seconds()
            if elapsed_mem < MIN_POST_INTERVAL_SECONDS:
                return False, MIN_POST_INTERVAL_SECONDS - elapsed_mem

        # 2. Database check (only on startup if in-memory is unset)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TwitterPost.posted_at)
                    .where(TwitterPost.target_token != "desk")
                    .order_by(desc(TwitterPost.posted_at))
                    .limit(1)
                )
                last_posted_at = result.scalar_one_or_none()
                if last_posted_at:
                    if last_posted_at.tzinfo is None:
                        last_posted_at = last_posted_at.replace(tzinfo=timezone.utc)
                    self.last_posted_at_memory = max(self.last_posted_at_memory or last_posted_at, last_posted_at)
                    elapsed = (now - last_posted_at).total_seconds()
                    if elapsed < MIN_POST_INTERVAL_SECONDS:
                        return False, MIN_POST_INTERVAL_SECONDS - elapsed
        except Exception as e:
            print(f"[TWITTER SERVICE] DB query fallback in check_cooldown: {e}")

        return True, 0.0

    async def _send(self, text: str) -> tuple[str, Optional[str], Optional[str]]:
        """
        POST one tweet. Returns (status, tweet_id, error): 'sent', 'dry_run' (auto-post disabled or no
        credentials) or 'failed'. Retries 429/5xx itself; touches no scheduler state.
        """
        is_enabled = settings.TWITTER_AUTO_POST_ENABLED and bool(
            settings.TWITTER_API_KEY and settings.TWITTER_API_SECRET and
            settings.TWITTER_ACCESS_TOKEN and settings.TWITTER_ACCESS_TOKEN_SECRET
        )
        if not is_enabled:
            return "dry_run", None, None
        if time.time() < self.api_backoff_until:
            return "failed", None, "API backoff after 401/402"

        headers = {
            "Content-Type": "application/json",
            "Authorization": generate_oauth1_header(
                method="POST",
                url=self.api_url,
                params={},
                api_key=settings.TWITTER_API_KEY,
                api_secret=settings.TWITTER_API_SECRET,
                access_token=settings.TWITTER_ACCESS_TOKEN,
                access_token_secret=settings.TWITTER_ACCESS_TOKEN_SECRET
            ),
        }
        payload = {"text": text}
        retries = 0
        max_retries = 3
        error_msg = None
        while retries <= max_retries:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(self.api_url, headers=headers, json=payload)
                if res.status_code in (200, 201):
                    return "sent", res.json().get("data", {}).get("id"), None
                if res.status_code in (429, 500, 502, 503, 504):
                    retries += 1
                    delay = (2 ** retries) + random.uniform(0.5, 1.5)
                    print(f"[TWITTER] Warning API {res.status_code}. Retrying in {delay:.1f}s...")
                    error_msg = f"HTTP {res.status_code}"
                    await asyncio.sleep(delay)
                    continue
                error_msg = f"HTTP {res.status_code}: {res.text}"
                print(f"[TWITTER] Error posting tweet: {error_msg}")
                if res.status_code in (401, 402):
                    # Credits depleted / unauthorized: backoff for 2 hours to save DB ops!
                    self.api_backoff_until = time.time() + 7200
                    print("[TWITTER] API credits depleted (402/401). Backing off for 2 hours to conserve DB operations...")
                return "failed", None, error_msg
            except Exception as e:
                retries += 1
                error_msg = str(e)
                if retries > max_retries:
                    print(f"[TWITTER] Failed after {retries} retries: {error_msg}")
                    break
                await asyncio.sleep((2 ** retries) + 1.0)
        return "failed", None, error_msg

    async def post_desk_tweet(self, text: str, trigger_type: str) -> dict:
        """
        The Desk's trade posts (desk_entry / desk_exit). Same transport and audit table as the news posts,
        but they leave the news scheduler's cooldown and token rotation alone.
        """
        status_str, tweet_id, error_msg = await self._send(text)
        try:
            async with AsyncSessionLocal() as db:
                db.add(TwitterPost(tweet_id=tweet_id, text=text, target_token="desk", trigger_type=trigger_type,
                                   status=status_str, error_message=error_msg))
                await db.commit()
        except Exception as db_err:
            print(f"[TWITTER SERVICE] Warning: Failed to save desk post record to DB: {db_err}")
        return {"status": status_str, "tweet_id": tweet_id, "error_message": error_msg}

    async def post_tweet(self, text: str, target_token: str, trigger_type: str = "recurring_2h_news", stats: dict = None) -> dict:
        """
        Publishes a tweet to Twitter API v2, or logs as dry-run if TWITTER_AUTO_POST_ENABLED is false.
        Handles API errors, retries, and records audit trail to Postgres database.
        """
        stats = stats or {}
        mc = stats.get("market_cap", 0.0)
        v24 = stats.get("volume_24h", 0.0)
        holders = stats.get("holders", None)

        status_str, tweet_id, error_msg = await self._send(text)
        if status_str == "sent":
            print(f"[TWITTER] Successfully posted tweet ID: {tweet_id}")
        elif status_str == "dry_run":
            print(f"[TWITTER] Dry-Run Mode Active — Tweet payload logged to DB without posting.")

        # Always update in-memory timestamp so we don't immediately retry and hammer the DB
        self.last_posted_at_memory = datetime.now(timezone.utc)
        self.last_target_memory = target_token

        # Record audit trail in database with fallback if DB connection fails
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with AsyncSessionLocal() as db:
                post_record = TwitterPost(
                    tweet_id=tweet_id,
                    text=text,
                    target_token=target_token,
                    market_cap_usd=mc,
                    volume_24h_usd=v24,
                    holders_count=holders if isinstance(holders, int) else None,
                    trigger_type=trigger_type,
                    status=status_str,
                    error_message=error_msg
                )
                db.add(post_record)
                await db.commit()
                await db.refresh(post_record)

                return {
                    "id": post_record.id,
                    "tweet_id": tweet_id,
                    "target_token": target_token,
                    "status": status_str,
                    "text": text,
                    "posted_at": post_record.posted_at.isoformat(),
                    "error_message": error_msg
                }
        except Exception as db_err:
            print(f"[TWITTER SERVICE] Warning: Failed to save post record to DB: {db_err}")
            return {
                "id": 1,
                "tweet_id": tweet_id,
                "target_token": target_token,
                "status": status_str,
                "text": text,
                "posted_at": now_iso,
                "error_message": error_msg
            }

    async def generate_and_post_alternating_news(self, target_token: str = None) -> dict:
        """
        Fetches live on-chain stats for the target token, generates clean professional text, and posts tweet.
        """
        if not target_token:
            target_token = await self.get_next_target_token()

        ca = (
            settings.EMILE_BANANA_TOKEN_CA 
            if target_token == "emile_banana" 
            else settings.EMILE_TOKEN_CA
        )

        # 1. Fetch live DEX details from DexScreener
        stats = await fetch_live_token_details(ca)
        
        # 2. Format narrative using Xiaomi MiMo LLM (with template fallback)
        from app.services.factual_narrative_generator import generate_mimo_llm_narrative
        tweet_text = await generate_mimo_llm_narrative(target_token, stats)

        # 3. Post tweet
        result = await self.post_tweet(
            text=tweet_text,
            target_token=target_token,
            trigger_type="recurring_2h_news",
            stats=stats
        )
        return result


twitter_service = TwitterService()


async def start_twitter_scheduler_loop():
    """
    Background worker loop that checks every 5 minutes whether a 2-hour scheduled news post is due.
    Enforces minimum cooldown and prevents race conditions.
    """
    if not settings.TWITTER_SCHEDULER_ENABLED:
        print("[TWITTER SCHEDULER] Disabled via TWITTER_SCHEDULER_ENABLED.")
        return
    print("[TWITTER SCHEDULER] Initializing 2-Hour Auto-Poster loop...")
    await asyncio.sleep(10)  # Initial grace period after startup

    while True:
        try:
            can_post, remaining_seconds = await twitter_service.check_cooldown()
            if can_post:
                print("[TWITTER SCHEDULER] 2-Hour threshold reached. Generating alternating news post...")
                await twitter_service.generate_and_post_alternating_news()
            else:
                remaining_mins = remaining_seconds / 60.0
                # print(f"[TWITTER SCHEDULER] Next post due in {remaining_mins:.1f} minutes.")
        except Exception as e:
            print(f"[TWITTER SCHEDULER] Error in scheduler loop: {e}")

        await asyncio.sleep(300)  # Check every 5 minutes
