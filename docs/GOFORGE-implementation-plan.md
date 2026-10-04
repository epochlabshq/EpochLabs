# GoForge: Implementation Plan

Source: `goforge-dev-brief.md` (4 Oct 2026). Route `/goforge`, nav `… Desk · GoForge · About`.

## Decisions taken (brief section 10, "to confirm with owner")

| Open question | Decision in code | Where to change |
|---|---|---|
| DexScreener `chainId` slug | `robinhood` (same as `live_market.py`) | `GOFORGE_DEX_CHAIN` |
| FeeRouter / hook / LP lock deployed at first launch? | No. Fields stay `null`, UI says "Rules: pending deployment" | config entry |
| Source of `why` | Owner copies it from the decision log into the config entry (`why_hash` must match the log). The API only echoes it | config entry |
| Gate numbers (30 trades, 7-day cooldown) | Env-driven defaults: `GOFORGE_GATE_MIN_TRADES=30`, `GOFORGE_COOLDOWN_DAYS=7` | `.env` |
| `model_fix` gate | No machine-readable source exists yet: `GOFORGE_MODEL_FIX_DONE` flag, default `false` | `.env` |
| `capital` gate | Golem wallet ETH balance >= `GOFORGE_LAUNCH_RESERVE_ETH` | `.env` |
| Gas in "net PnL after gas" | `golem_swaps` has no gas column. Realized PnL minus `GOFORGE_GAS_ETH_PER_TRADE` x 2 x closed trades (default 0, to be set by owner) | `.env` |

## Backend

1. `app/core/goforge_config.py`: load + validate `backend/config/goforge.launches.json` (required `id`, `ca`, `launch_tx`; address regex; unique id/ca), mtime-cached. Exposes `goforge_cas()`.
   File lives in `backend/config/` because both Dockerfiles only ship `backend/`. Override with `GOFORGE_LAUNCHES_PATH`.
2. Trading exclusion: `Settings.desk_excluded_tokens` unions in `goforge_cas()`, so executor, paper trader, discovery and Watching all exclude them. `desk_wallet_sync` also skips GoForge CAs so a token received by the Golem wallet is never recorded as a trade.
3. `app/db/goforge_schema.py`: `goforge_launches`, `goforge_snapshots` (+ extra columns `pool_active_at`, `pair_url`, `decimals`, `total_supply`, `deployer`, `top10_pct`, `last_source_ok_at`). DB trigger: rows cannot be deleted, a locked verdict cannot change, `ca` / `launch_tx` / `launched_at` cannot be rewritten.
4. `app/services/goforge.py` (pure, unit-tested):
   - `compute_verdict` / `lock_verdict`: pending until 48h, then `reached_30k` iff `peak >= 30000`, `stalled` otherwise; never changes once set.
   - peak MC only from stored snapshots inside the 48h window.
   - `evaluate_gate`: 5 conditions, status `locked | ready | forging | cooldown`, `blocked_by`.
   - `serialize_launch`: `null` stays `null` (never 0), `stale` + `stale_age_s`, links, share text with the ticker written without `$`.
   - **Visibility rule**: a launch is serialized only once its pool is active (`pool_active_at` set). Before that nothing (name, symbol, CA) reaches the API, WS or HTML.
5. `app/services/goforge_sources.py`: Blockscout (token, launch tx, holders, top holders, EPC burns), DexScreener (`/tokens/v1/{chain}/{ca}`), holder-count RPC fallback.
6. `app/services/goforge_worker.py`: cycle every 60s (config is re-read each cycle, new entry picked up with no restart). Does no DB work when the config is empty. Locked-verdict launches refresh every 10 min to protect the DB ops budget. Broadcasts `goforge_update`, `goforge_verdict`, `goforge_burn`.
7. `app/api/goforge_endpoints.py`: `GET /api/goforge` (15 s cache), `GET /api/goforge/{id}/history` (48h MC/price series). Gate inputs reuse the Desk's cached snapshot.
8. `main.py` wiring. Side fix: `desk_worker.py` used `time.time()` without `import time`.

## Frontend

- `/goforge` page + layout metadata + OG image, nav entry `GoForge` between Desk and About.
- Components: hero + gate badge, gate list (check / progress), totals bar, token cards (countdown 48h, verdict badge with equal weight for Stalled, 48h sparkline with dashed $30K line, copyable CA, links), Why dialog (copyable `why_hash`), hook rules (null hidden, "Rules: pending deployment"), footer with addresses and disclaimer, empty state.
- No Buy button anywhere. Share text strips `$`. Mobile: full-width cards, 16px gutters, no horizontal scroll.
- Store + `useGoForge` + WS events `goforge_update|verdict|burn`.

## Tests

`backend/tests/test_goforge.py` (unittest, matches the repo): config validation, verdict lock and immutability, peak only from in-window snapshots, gate matrix, null handling and stale flag, pre-pool invisibility (API payload and WS payload), exclusion of every GoForge CA (settings, `select_candidates`, wallet sync filter), share text without `$`, worker cycle with fake sources.
Frontend: `tsc --noEmit`, `eslint`, `next build`, then a browser check on desktop and mobile widths against a mock API.
