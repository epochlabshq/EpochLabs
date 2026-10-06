# GoForge Registry (Community Launch): Implementation Plan

Source: `goforge-registry-dev-brief.md` (6 Oct 2026). It replaces the first GoForge brief (the Launch Gate page).
The live-data half of the old build (Blockscout + DexScreener watcher, 48h verdict, snapshots, trading exclusion)
is reused for the archive of forged tokens. The Launch Gate is removed.

## Decisions taken for the open questions (brief section 13)

Everything below is a default in `app/core/config.py` / env. None of it is a claim about the real world.

| Open question | Decision in code | Consequence |
|---|---|---|
| Pons: split fee to two addresses? | **Assume no.** A `FeeSplitter` (one clone per token) receives the creator fee | `contracts/contracts/FeeSplitter*.sol`, tested on a local Uniswap V2 |
| Pons: can the agent launch via API? | **Unknown.** `GF_LAUNCH_MODE=manual` (default): Golem schedules the launch, the team launches on Pons and registers the CA through an admin endpoint. A `pons` backend is a stub that refuses until the Pons API is known | Nothing launches by itself yet |
| Submit fee | `GF_SUBMIT_FEE_EPC=1000` (placeholder) | Owner to set |
| Vote threshold | `GF_VOTE_MIN_EPC=1000`, wallet age 7 days | Owner to set |
| X API tier | `/2/users/me` with the user's own OAuth token (verified, created_at, public_metrics). The Free tier rate-limits it hard | Owner to confirm tier |
| Daily schedule | As in the brief, all in env (`GF_*_HOUR_UTC`) | |
| Manual review | Admin API behind `GF_ADMIN_TOKEN` (list pending, approve, reject with reason). No admin UI | Review is mandatory: nothing reaches the vote list without it |

Interpretations the brief leaves open (flagged here so the owner can overrule):
- **Pool normalisation (5.1):** credibility and Golem are absolute 0-100 scores from the formulas in 5.2/5.3; only the vote component is relative (5.4). Min-max normalising them inside a pool of 2-3 ideas would exaggerate tiny differences.
- **Narrative survival to 0-100:** `lift = cluster rate / chain baseline`, `score = clamp(lift x 50, 0, 100)` (lift 1 = 50, lift >= 2 = 100). Cluster with `n_resolved < 20` = 50.
- **Lore quality:** 0 at 50 chars, 100 at 250, flat to 600, then eases down to 70 at 1000.
- **Automated image moderation:** the repo has no NSFW or logo model. The automatic stage checks only format, size, 1:1, hash and duplicates; NSFW and brand logos are caught by the mandatory manual review. A pluggable `ImageModerator` hook exists.
- **Stock tickers:** curated list in `backend/config/gf_stock_tickers.json` (large NYSE/Nasdaq names), extendable by env. It is not an exchange feed.

## Phases

1. **Foundations.** Settings, schema (`gf_creators`, `gf_rounds`, `gf_ideas`, `gf_votes`, `gf_launches`, plus `gf_distributions`, `gf_nonces`, `gf_wallet_changes`), round schedule logic, remove the Launch Gate.
2. **Validation and moderation.** Form rules, image checks (ratio, size, format, SHA-256), brand/person deny list, ticker collision (Robinhood tokens + stock list), lore safety (existing worker), duplicate detection (cosine >= 0.95).
3. **Identity.** SIWE (EIP-4361) with signature recovery, X OAuth 2.0 PKCE, session cookie, `wallet <-> x_user_id` linking with the one-account-one-wallet rule and a change log.
4. **Submit.** `POST /ideas`: session, fee tx verification (EPC Transfer wallet -> dead, amount, unused), 100-slot cap, 1 idea per wallet per day, image storage by hash.
5. **Vote.** EIP-712 gasless votes, eligibility (balance, wallet age, not own idea), 30 changes per hour, balance re-check at close, public vote list after close.
6. **Scoring and rounds.** 40/30/30 score, tie-break, 7-day win cap, no-launch rules, full scoreboard, the round worker (submit, review, vote, scoring, announced, launched / no_launch).
7. **Launch, fee split, archive.** Launch time from the hourly survival data, manual launch registration, `FeeSplitter` contract + tests, distribution indexer, live tracking and 48h verdict through the existing watcher, trading exclusion for every launched CA.
8. **X posts.** The three posts (winner, live, verdict), ticker without `$`, dry-run unless enabled.
9. **API and WebSocket.** All endpoints of section 10, events `gf_vote`, `gf_phase`, `gf_winner`, `gf_launch`, `gf_verdict`.
10. **Frontend.** `/goforge`: hero with countdown and totals, today's pool with vote buttons, submit flow (wallet + X), scoreboard, forged archive, how it works, footer.

## Tests

Backend unittest suites per phase (pure logic with fake data, signature round-trips with real keys, mocked RPC/X), hardhat tests for `FeeSplitter`, then `tsc`, `eslint`, `next build` and a browser pass on desktop and mobile against a mock API.

## Not verifiable here

Pons launch, a real X OAuth round trip, real Blockscout/RPC and the production database. Each is behind an interface or env switch, covered by unit tests with fakes, and listed in the final report as unverified.

## Operator runbook

1. **Backend env** (see `backend/.env.example`): `GF_SESSION_SECRET` (long random), `GF_ADMIN_TOKEN`, the X app (`GF_X_CLIENT_ID`,
   `GF_X_CLIENT_SECRET`, `GF_X_REDIRECT_URI`), `GF_SIWE_DOMAIN`/`GF_SIWE_URI`/`GF_FRONTEND_URL` for the real site, `GF_TEAM_WALLETS`,
   and the real `GF_SUBMIT_FEE_EPC` / `GF_VOTE_MIN_EPC`. Keep `GF_X_POST_ENABLED=false` until the dry runs in `gf_posts` look right.
2. **Cookie and X redirect.** The session is an HttpOnly cookie, so the browser must reach the API through the site
   (`/api/*` is rewritten to `BACKEND_URL`). Register the X redirect as `https://<site>/api/goforge/auth/x/callback` and use the same
   value for `GF_X_REDIRECT_URI`.
3. **Fee splitter.** `cd contracts && EPC_TOKEN=0x... npx hardhat run scripts/deploy-fee-splitter.js --network robinhood`, then set
   `GF_SPLITTER_FACTORY`. The registrar is the agent key.
4. **Review.** `GET /api/goforge/admin/review` lists ideas with their flags (every image needs a visual check: there is no NSFW or logo
   classifier). `POST /api/goforge/admin/ideas/{id}/review` with `{"status":"approved"}` or `{"status":"rejected","reason":"..."}`. Header
   `X-Admin-Token`, optional `X-Admin-Name`. Ideas approved after 12:00 UTC still join the pool until the vote closes.
5. **Launch (manual mode).** After the 20:05 announcement: create the splitter with the winner's verified wallet
   (`CREATOR=0x... IDEA_ID=<id> npx hardhat run scripts/create-splitter.js --network robinhood`), launch on Pons with that splitter as the
   creator-fee recipient, then `POST /api/goforge/admin/launch {"idea_id","ca","launch_tx","splitter_address"}`. The CA is excluded from
   Golem's trading at once and the watcher starts the 48 hour verdict.
6. **Open the page.** Set `NEXT_PUBLIC_GOFORGE_LIVE=true` on the frontend and redeploy. Until then `/goforge` shows "Coming soon".

## Result

| Layer | Tests |
|---|---|
| Backend, pure rules (`test_gf_core`) | 96 |
| Backend, chain / launch planning / posts (`test_gf_launch_chain`) | 44 |
| Backend, SQL and triggers on a real PostgreSQL (`test_gf_store`) | 34 |
| Backend, flows on a real PostgreSQL with a fake chain (`test_gf_service`) | 54 |
| Backend, round worker (`test_gf_worker`) | 13 |
| Backend, HTTP API incl. SIWE and X login (`test_gf_api`) | 50 |
| Backend, launch tracking (`test_goforge`) | 62 |
| Contracts (`FeeSplitter.test.js`, local Uniswap V2) | 17 |
| Frontend helpers (`node --test src/lib/gf.test.ts`) | 15 |

Browser pass against the real routers on an embedded PostgreSQL: SIWE login, X link, form validation, fee payment, submit, vote
(and moving it), scoreboard, archive card, mobile layout, Coming-soon mode.

## Not verified here (needs the real world)

- Pons: whether the agent can launch, and whether its creator fee can go to a splitter address. The manual flow assumes both questions
  answered "launch by hand, fee recipient = splitter".
- A real X OAuth round trip (the API calls are covered with fakes), and the X API tier limits on `/2/users/me`.
- Real Blockscout / Alchemy responses for wallet age and the fee tx (decoders are tested on constructed receipts).
- The keeper transaction on a real chain (the signed transaction is checked offline).
- FeeSplitter on Robinhood Chain mainnet (tested on a local Uniswap V2; the MEV note in the contract applies).
- Production database operation budget under real traffic (an idle worker cycle does no database work; the vote and round endpoints are cached 5 s).
