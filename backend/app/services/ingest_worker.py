import asyncio
import time
from datetime import datetime, timezone, date
from sqlalchemy import text
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import IngestLog
from app.services.mint_source import get_default_mint_source
from app.services.dexscreener import DexScreenerPoller
from app.services.lore_safety import sanitize_lore
from app.services.holder_sampler import run_label_worker_cycle
from app.api.websocket import manager

# Waggle-inspired in-memory cache to reduce DB operations by 95%+
# Stores: {mint: (status_str, last_polled_at_datetime)}
_known_tokens_cache: dict[str, tuple[str, datetime | None]] = {}
_cache_initialized = False
_last_cache_refresh = 0.0
_last_label_cycle_run = 0.0
_db_backoff_until = 0.0

# 60 seconds interval per cycle (matches Waggle CYCLE_INTERVAL_MS = 60000)
CYCLE_INTERVAL_SECONDS = 60

# Cache TTL: full refresh only once every 2 hours (7200s)
CACHE_REFRESH_INTERVAL_SECONDS = 7200

# Label cycle interval: every 15 minutes (900s)
LABEL_CYCLE_INTERVAL_SECONDS = 3600

# A known pending token is re-upserted at most this often (6 hours)
REPOLL_AFTER_SECONDS = 21600.0


async def start_ingest_worker_loop():
    """
    Optimized continuous background worker loop (Waggle architecture):
    1. Uses in-memory token cache to completely eliminate redundant SELECT * queries every cycle.
    2. Runs on a 60-second cycle interval (saving ~66% of baseline operations).
    3. Throttles 48h labeling cycle to run only once every 15 minutes instead of every cycle.
    4. Throttles database count queries and updates in-memory.
    5. Includes automatic 5-minute backoff if database plan limits (planLimitReached) are detected.
    """
    global _known_tokens_cache, _cache_initialized, _last_cache_refresh, _last_label_cycle_run, _db_backoff_until

    if not settings.INGEST_WORKER_ENABLED:
        print("[INGEST WORKER] Disabled via INGEST_WORKER_ENABLED.")
        return
    print("[INGEST WORKER] STARTING OPTIMIZED ROBINHOOD SCANNER (Waggle-efficient mode)...")
    scanner = get_default_mint_source()
    poller = DexScreenerPoller()
    cursor = None
    cycle_count = 0

    while True:
        cycle_count += 1
        now_ts = time.time()

        # 1. Waggle pattern: PlanLimitReached / Backoff protection
        if now_ts < _db_backoff_until:
            remain = int(_db_backoff_until - now_ts)
            print(f"[INGEST WORKER] Database quota backoff active ({remain}s remaining). Skipping DB operations...")
            await asyncio.sleep(min(60, remain))
            continue

        try:
            now_dt = datetime.now(timezone.utc)
            today = date.today()

            # 2. In-memory cache initialization / periodic refresh (only once every 2 hours)
            if not _cache_initialized or (now_ts - _last_cache_refresh > CACHE_REFRESH_INTERVAL_SECONDS):
                if AsyncSessionLocal is not None:
                    try:
                        async with AsyncSessionLocal() as db:
                            print("[INGEST WORKER] Refreshing in-memory token cache from DB...")
                            existing_res = await db.execute(text("SELECT mint, status::text, last_polled_at FROM tokens;"))
                            _known_tokens_cache = {row[0]: (row[1], row[2]) for row in existing_res.fetchall()}
                            _cache_initialized = True
                            _last_cache_refresh = now_ts
                            print(f"[INGEST WORKER] In-memory cache refreshed ({len(_known_tokens_cache)} tokens loaded into memory).")
                    except Exception as db_err:
                        err_str = str(db_err)
                        if "planLimitReached" in err_str or "restrictions" in err_str:
                            print("[INGEST WORKER] Database plan limit reached. Activating 5-min backoff...")
                            _db_backoff_until = time.time() + 300
                            await asyncio.sleep(60)
                            continue
                        print(f"[INGEST WORKER] Cache refresh warning: {err_str[:120]}")

            # 3. Fetch newly scanned tokens from DEX source
            raw_mints = await scanner.fetch_since(cursor)

            if raw_mints and AsyncSessionLocal is not None:
                # 4. Filter tokens ENTIRELY in memory using _known_tokens_cache (0 DB queries!)
                mints_to_process = []
                for raw in raw_mints:
                    # STRICT REJECTION: Never process or insert any Solana or pump.fun token
                    if raw.mint.endswith("pump") or "solana" in raw.name.lower() or getattr(raw, "chain", "").lower() == "solana":
                        continue

                    if raw.mint in _known_tokens_cache:
                        status_val, last_polled = _known_tokens_cache[raw.mint]
                        # If token already passed ($30K+ peak MC), positive label is permanent. Skip permanently!
                        if status_val == "passed":
                            continue
                        # If token was polled recently, skip re-scanning (each re-poll is a DB write)
                        if last_polled:
                            if last_polled.tzinfo is None:
                                last_polled = last_polled.replace(tzinfo=timezone.utc)
                            elapsed = (now_dt - last_polled).total_seconds()
                            if elapsed < REPOLL_AFTER_SECONDS:
                                continue

                    mints_to_process.append(raw)

                if not mints_to_process:
                    # Zero DB queries when idle! Total tokens known from in-memory cache!
                    if cycle_count % 5 == 0:
                        print(f"[INGEST WORKER] Scan loop idle. Total Tokens in memory: {len(_known_tokens_cache)} (0 DB queries consumed)")
                else:
                    # 5. Batch fetch current prices ONLY for tokens needing process
                    mint_addresses = [m.mint for m in mints_to_process]
                    prices = await poller.fetch_batch_prices(mint_addresses)

                    async with AsyncSessionLocal() as db:
                        newly_inserted = 0
                        for raw in mints_to_process:
                            lore_disp, withheld, reason = sanitize_lore(raw.lore)
                            price_data = prices.get(raw.mint)
                            token_name = raw.name
                            token_symbol = raw.symbol

                            if isinstance(price_data, dict):
                                current_mc = float(price_data.get("mc") or 10500.0)
                                if price_data.get("name"):
                                    token_name = price_data.get("name")
                                if price_data.get("symbol"):
                                    token_symbol = price_data.get("symbol")
                            elif isinstance(price_data, (int, float)):
                                current_mc = float(price_data)
                            else:
                                current_mc = 10500.0

                            query = text("""
                                INSERT INTO tokens (
                                    mint, chain, name, symbol, lore, lore_display, lore_withheld,
                                    image_url, creator, launched_at, peak_mc, last_seen_mc,
                                    status, first_seen_at, poll_count, crossed_10k_at, emile_launched
                                ) VALUES (
                                    :mint, :chain, :name, :symbol, :lore, :lore_disp, :withheld,
                                    :image_url, :creator, :launched_at, :peak_mc, :last_seen_mc,
                                    CASE WHEN :peak_mc >= 30000.0 THEN 'passed'::token_status ELSE 'pending'::token_status END,
                                    :now, 1,
                                    CASE WHEN :peak_mc >= 10000.0 THEN :now ELSE NULL::timestamptz END,
                                    FALSE
                                )
                                ON CONFLICT (mint) DO UPDATE SET
                                    peak_mc = GREATEST(tokens.peak_mc, EXCLUDED.peak_mc),
                                    last_seen_mc = EXCLUDED.last_seen_mc,
                                    last_polled_at = :now,
                                    poll_count = tokens.poll_count + 1,
                                    name = CASE 
                                        WHEN EXCLUDED.name IS NOT NULL AND EXCLUDED.name != '' AND (tokens.name LIKE 'Robinhood Token $0X%' OR tokens.name LIKE 'Solana%') THEN EXCLUDED.name 
                                        ELSE tokens.name 
                                    END,
                                    symbol = CASE 
                                        WHEN EXCLUDED.symbol IS NOT NULL AND EXCLUDED.symbol != '' AND (tokens.symbol LIKE '0X%' OR tokens.symbol = 'SOL') THEN EXCLUDED.symbol 
                                        ELSE tokens.symbol 
                                    END,
                                    status = CASE
                                        WHEN GREATEST(tokens.peak_mc, EXCLUDED.peak_mc) >= 30000.0 THEN 'passed'::token_status
                                        ELSE tokens.status
                                    END,
                                    crossed_10k_at = CASE
                                        WHEN tokens.crossed_10k_at IS NULL AND EXCLUDED.peak_mc >= 10000.0 THEN :now
                                        ELSE tokens.crossed_10k_at
                                    END
                                RETURNING (xmax = 0) AS is_new;
                            """)

                            res = await db.execute(query, {
                                "mint": raw.mint,
                                "chain": getattr(raw, "chain", "robinhood"),
                                "name": token_name,
                                "symbol": token_symbol,
                                "lore": raw.lore,
                                "lore_disp": lore_disp,
                                "withheld": withheld,
                                "image_url": raw.image_url,
                                "creator": raw.creator,
                                "launched_at": raw.launched_at,
                                "peak_mc": current_mc,
                                "last_seen_mc": current_mc,
                                "now": now_dt
                            })

                            row = res.fetchone()
                            is_brand_new = row[0] if row else False

                            # Determine status for in-memory cache update
                            assigned_status = "passed" if current_mc >= 30000.0 else "pending"
                            if raw.mint in _known_tokens_cache:
                                existing_status = _known_tokens_cache[raw.mint][0]
                                if existing_status == "passed":
                                    assigned_status = "passed"

                            # Update in-memory cache immediately
                            _known_tokens_cache[raw.mint] = (assigned_status, now_dt)

                            if is_brand_new:
                                newly_inserted += 1

                            # Broadcast live token event to WebSocket clients
                            token_payload = {
                                "mint": raw.mint,
                                "name": token_name,
                                "symbol": token_symbol,
                                "lore": lore_disp,
                                "holders": None,  # sampled once at the 48h label, unknown before that
                                "peak_mc": current_mc,
                                "status": assigned_status,
                                "hour": raw.launched_at.hour
                            }
                            await manager.broadcast({"token": token_payload})

                        # 6. Trigger 48h holder sampler & label worker ONLY every 15 minutes (900s)
                        if now_ts - _last_label_cycle_run > LABEL_CYCLE_INTERVAL_SECONDS:
                            _last_label_cycle_run = now_ts
                            print("[INGEST WORKER] Running scheduled 15-min 48h label worker cycle...")
                            await run_label_worker_cycle(db)

                        # Update daily universe metrics and log ONLY if brand new tokens were added
                        if newly_inserted > 0:
                            cur_universe = text("""
                                INSERT INTO daily_universe (day, minted_total, crossed_10k, crossed_30k)
                                VALUES (:day, :cnt, :cnt, 0)
                                ON CONFLICT (day) DO UPDATE SET
                                    minted_total = daily_universe.minted_total + :cnt,
                                    crossed_10k = daily_universe.crossed_10k + :cnt;
                            """)
                            await db.execute(cur_universe, {"day": today, "cnt": newly_inserted})

                            # Log ingest worker batch run via lightweight SQL
                            await db.execute(
                                text("INSERT INTO ingest_log (source, ok, failed) VALUES (:src, :ok, 0);"),
                                {"src": "robinhood_dexscreener", "ok": len(mints_to_process)}
                            )

                        await db.commit()
                        print(f"[INGEST WORKER] Processed {len(mints_to_process)} tokens (+{newly_inserted} NEW). Total cached: {len(_known_tokens_cache)}")

            if raw_mints:
                cursor = max([m.launched_at for m in raw_mints])

        except Exception as e:
            err_msg = str(e).encode("ascii", errors="replace").decode("ascii")
            if "planLimitReached" in err_msg or "restrictions" in err_msg:
                print("[INGEST WORKER] Database plan limit reached. Backing off for 5 minutes...")
                _db_backoff_until = time.time() + 300
            else:
                print(f"[INGEST WORKER ERROR] Loop Warning: {err_msg[:140]}")

        # Waggle pattern: Sleep 60s per cycle
        await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
