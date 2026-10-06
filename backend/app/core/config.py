import os
from dotenv import load_dotenv
load_dotenv(override=True)
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Epoch Labs"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    NETWORK: str = os.getenv("NETWORK", "mainnet")

    @property
    def RAW_DATABASE_URL(self) -> str:
        url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "postgresql://postgres:postgres@localhost:5432/emile_db"
        return url.strip()

    @property
    def DATABASE_URL_ASYNC(self) -> str:
        url = self.RAW_DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        if "sslmode=require" in url:
            url = url.replace("sslmode=require", "ssl=require")
        return url

    @property
    def DATABASE_URL_SYNC(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        elif url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        elif url.startswith("postgresql+psycopg2://"):
            url = url.replace("postgresql+psycopg2://", "postgresql://", 1)
        return url

    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

    # Data Provider Endpoints & Keys
    HELIUS_API_KEY: str = os.getenv("HELIUS_API_KEY", "")

    @property
    def CLEAN_HELIUS_API_KEY(self) -> str:
        key = self.HELIUS_API_KEY.strip()
        if "api-key=" in key:
            key = key.split("api-key=")[-1].split("&")[0]
        elif key.startswith("http://") or key.startswith("https://"):
            key = key.rstrip("/").split("/")[-1]
        return key

    DEFAULT_CHAIN: str = os.getenv("DEFAULT_CHAIN", "robinhood")
    DEFAULT_CHAIN_LABEL: str = os.getenv("DEFAULT_CHAIN_LABEL", "Robinhood Chain Mainnet")
    DEXSCREENER_API_BASE: str = "https://api.dexscreener.com/latest/dex"
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

    # Object Storage for 64x64 WebP thumbnails
    STORAGE_LOCAL_PATH: str = os.getenv("STORAGE_LOCAL_PATH", "./storage/thumbnails")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "emile-thumbnails")
    CDN_BASE_URL: str = os.getenv("CDN_BASE_URL", "https://cdn.emile.xyz/t")

    # ML & Jar Threshold Parameters
    # Single source of truth: trainer, /api/state, /api/methodology.json and the
    # frontend all read these. Never hardcode them anywhere else.
    # CAPACITY_D = feature columns produced by app.ml.features.extract_features:
    # hour sin/cos (2) + dow one-hot (7) + holders_log (1) + lore_len, lore_missing,
    # name_tokens (3) + lore PCA (24) = 37.
    CAPACITY_D: int = 37
    AUC_TARGET: float = 0.60
    AUC_FLOOR: float = 0.50
    DELTA_CONFIDENCE: float = 0.05

    # Hard gates (aligned with the published methodology)
    GATE_N_SAMPLES: int = 2000
    GATE_N_POSITIVE: int = 200
    GATE_AUC_STD_MAX: float = 0.05
    GATE_TIME_SPLIT_GAP_MAX: float = 0.04
    # Sand level is capped here while any gate fails
    JAR_GATE_CAP: float = 0.95

    # Model worker: retrain when the labeled set changes, checked every N seconds
    MODEL_WORKER_INTERVAL_SECONDS: int = int(os.getenv("MODEL_WORKER_INTERVAL_SECONDS", "21600"))
    # Only one deployment should run the writers below against a database. A local backend that shares the
    # production database should turn off what production already runs (see backend/.env.example).
    INGEST_WORKER_ENABLED: bool = os.getenv("INGEST_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")
    TWITTER_SCHEDULER_ENABLED: bool = os.getenv("TWITTER_SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes")
    MODEL_WORKER_ENABLED: bool = os.getenv("MODEL_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")

    # Target Token Contract Addresses (CAs) & Axiom URLs
    EPOCH_TOKEN_CA: str = os.getenv("EPOCH_TOKEN_CA", os.getenv("EMILE_TOKEN_CA", "0xb71463fbe6a6edef8d9d8cb0ccb5ab84cd0a353e"))
    EPOCH_AXIOM_URL: str = os.getenv("EPOCH_AXIOM_URL", "https://axiom.trade/token/0xb71463fbe6a6edef8d9d8cb0ccb5ab84cd0a353e?chain=robinhood")
    EMILE_TOKEN_CA: str = EPOCH_TOKEN_CA
    EMILE_BANANA_TOKEN_CA: str = os.getenv("EMILE_BANANA_TOKEN_CA", "0x3c51485b11d52f90c251e74875a8b93c81027274")
    EMILE_AXIOM_URL: str = EPOCH_AXIOM_URL
    EMILE_BANANA_AXIOM_URL: str = os.getenv("EMILE_BANANA_AXIOM_URL", "https://axiom.trade/token/0x3c51485b11d52f90c251e74875a8b93c81027274?chain=robinhood&chains=robinhood,bnb")

    # Robinhood Chain (Epochs page). Addresses from contracts/deployments/robinhood.json.
    CHAIN_ID: int = 4663
    CHAIN_RPC_URL: str = os.getenv("CHAIN_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
    # Alchemy for Robinhood Chain: point reads (balances, eth_call) and asset transfers. Its free tier caps
    # eth_getLogs at 10 blocks, so log scans (indexers, holder counts) stay on CHAIN_RPC_URL.
    RH_MAINNET_RPC_URL: str = os.getenv("RH_MAINNET_RPC_URL", "")
    BLOCKSCOUT_BASE: str = os.getenv("BLOCKSCOUT_BASE", "https://robinhoodchain.blockscout.com")
    EPOCH_LAUNCHER: str = os.getenv("EPOCH_LAUNCHER", "0x75fd64Cc8D57c529f34089Ac9083E704c23F0D8B")
    EPOCH_TOKEN_TEMPLATE: str = os.getenv("EPOCH_TOKEN_TEMPLATE", "0x96508719c110a341708546de78051e020f51D264")
    EPOCH_LAUNCHER_OWNER: str = os.getenv("EPOCH_LAUNCHER_OWNER", "0x2f9647850484feCcdf316164b4606251Bd4A3bd1")
    GOLEM_AGENT: str = os.getenv("GOLEM_AGENT", "0x560Eb3767434006b3278810f906d7677C38914aD")
    GOLEM_WALLET: str = os.getenv("GOLEM_WALLET", "0x49EdF5f24216e02EEb6a947cC3dF0CDB6B84582C")
    UNISWAP_V2_ROUTER: str = os.getenv("UNISWAP_V2_ROUTER", "0x89e5db8b5aa49aa85ac63f691524311aeb649eba")
    WETH: str = os.getenv("WETH", "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73")
    # Unset until the burn rules are published (Epoch IV stays locked without it)
    EPC_BURN_ADDRESS: str = os.getenv("EPC_BURN_ADDRESS", "")
    # Unset until the repo is public (Epoch VI stays locked without it), e.g. "epochlabs/golem"
    GOLEM_GITHUB_REPO: str = os.getenv("GOLEM_GITHUB_REPO", "")
    GOLEM_RELEASE_TAG: str = os.getenv("GOLEM_RELEASE_TAG", "golem-v1")
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    # Launcher deploy block: nothing Epochs cares about can predate it
    EPOCH_SCAN_FROM_BLOCK: int = int(os.getenv("EPOCH_SCAN_FROM_BLOCK", "78708782"))
    # Node limit for eth_getLogs without an address filter is 30,000 blocks
    EPOCH_LOG_CHUNK_BLOCKS: int = int(os.getenv("EPOCH_LOG_CHUNK_BLOCKS", "30000"))
    EPOCH_MAX_CHUNKS_PER_TICK: int = int(os.getenv("EPOCH_MAX_CHUNKS_PER_TICK", "40"))
    EPOCH_WATCHER_INTERVAL_SECONDS: int = int(os.getenv("EPOCH_WATCHER_INTERVAL_SECONDS", "3600"))
    EPOCH_WATCHER_ENABLED: bool = os.getenv("EPOCH_WATCHER_ENABLED", "true").lower() in ("true", "1", "yes")

    UNISWAP_V2_FACTORY: str = os.getenv("UNISWAP_V2_FACTORY", "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f")

    # The Desk (live trading page). Trading values are defaults until the Open questions are decided.
    DESK_ENTRY_THRESHOLD: float = float(os.getenv("DESK_ENTRY_THRESHOLD", "0.65"))
    DESK_POSITION_SIZE_ETH: float = float(os.getenv("DESK_POSITION_SIZE_ETH", "0.05"))
    DESK_MAX_OPEN: int = int(os.getenv("DESK_MAX_OPEN", "3"))
    DESK_TP_MC_USD: float = float(os.getenv("DESK_TP_MC_USD", "30000"))
    DESK_SL_MC_USD: float = float(os.getenv("DESK_SL_MC_USD", "5000"))
    DESK_MAX_HOLD_H: int = int(os.getenv("DESK_MAX_HOLD_H", "48"))
    DESK_MIN_LIQ_USD: float = float(os.getenv("DESK_MIN_LIQ_USD", "5000"))
    DESK_START_ETH: float = float(os.getenv("DESK_START_ETH", "1.0"))
    DESK_HEARTBEAT_WARN_SECONDS: int = int(os.getenv("DESK_HEARTBEAT_WARN_SECONDS", "5400"))
    DESK_DROPPED_VISIBLE_H: int = int(os.getenv("DESK_DROPPED_VISIBLE_H", "6"))
    DESK_DROPPED_REVEAL_H: int = int(os.getenv("DESK_DROPPED_REVEAL_H", "48"))
    DESK_WATCHING_LIMIT: int = int(os.getenv("DESK_WATCHING_LIMIT", "10"))
    DESK_CLOSED_LIMIT: int = int(os.getenv("DESK_CLOSED_LIMIT", "50"))
    # Tokens Golem must never buy: EPC, the team's other tokens, plus a comma-separated env list
    DESK_EXCLUDED_TOKENS_EXTRA: str = os.getenv("DESK_EXCLUDED_TOKENS", "")
    # X auto-post of entries/exits. Live sending also needs TWITTER_AUTO_POST_ENABLED; otherwise posts are dry runs.
    DESK_X_POST_ENABLED: bool = os.getenv("DESK_X_POST_ENABLED", "false").lower() in ("true", "1", "yes")
    DESK_X_POST_DELAY_SECONDS: int = int(os.getenv("DESK_X_POST_DELAY_SECONDS", "600"))
    DESK_X_POST_MAX_ATTEMPTS: int = int(os.getenv("DESK_X_POST_MAX_ATTEMPTS", "3"))
    # Trades older than this when first announced (history backfill) are never posted
    DESK_X_POST_MAX_AGE_H: int = int(os.getenv("DESK_X_POST_MAX_AGE_H", "6"))
    DESK_PUBLIC_URL: str = os.getenv("DESK_PUBLIC_URL", "https://epochlabs.run/desk")
    # Executor: "off" (default), "dry_run" (evaluate and log only, writes nothing) or "live" (needs a signer)
    # SIMULATION: log hypothetical buys/sells at real prices, independent of the trade gate. No funds involved.
    DESK_PAPER_ENABLED: bool = os.getenv("DESK_PAPER_ENABLED", "false").lower() in ("true", "1", "yes")
    DESK_EXECUTOR_MODE: str = os.getenv("DESK_EXECUTOR_MODE", "off").lower()
    DESK_MAX_WALLET_FRACTION: float = float(os.getenv("DESK_MAX_WALLET_FRACTION", "0.2"))
    DESK_GAS_RESERVE_ETH: float = float(os.getenv("DESK_GAS_RESERVE_ETH", "0.005"))
    DESK_SLIPPAGE_BPS: int = int(os.getenv("DESK_SLIPPAGE_BPS", "500"))
    DESK_TX_DEADLINE_SECONDS: int = int(os.getenv("DESK_TX_DEADLINE_SECONDS", "120"))
    DESK_ENTERING_TIMEOUT_MINUTES: int = int(os.getenv("DESK_ENTERING_TIMEOUT_MINUTES", "10"))
    # Watched tokens whose holder count is recounted onchain per Desk worker cycle (stalest first)
    # Watching shows a token while its live market cap is at or above this (the feed's $10K entry bar)
    DESK_WATCH_MIN_MC_USD: float = float(os.getenv("DESK_WATCH_MIN_MC_USD", "10000"))
    DESK_HOLDERS_BATCH: int = int(os.getenv("DESK_HOLDERS_BATCH", "20"))
    DESK_WORKER_INTERVAL_SECONDS: int = int(os.getenv("DESK_WORKER_INTERVAL_SECONDS", "1800"))
    DESK_WORKER_ENABLED: bool = os.getenv("DESK_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def desk_excluded_tokens(self) -> frozenset[str]:
        extra = [a.strip() for a in self.DESK_EXCLUDED_TOKENS_EXTRA.split(",") if a.strip()]
        # Every GoForge launch is Golem's own token: it must never buy or sell it (goforge.launches.json)
        from app.core.goforge_config import goforge_cas
        return frozenset(a.lower() for a in [self.EPOCH_TOKEN_CA, self.EMILE_BANANA_TOKEN_CA, *extra]) | goforge_cas()

    # GoForge: live tracking of every launched token (Blockscout + DexScreener) and the 48h verdict
    GOFORGE_WORKER_ENABLED: bool = os.getenv("GOFORGE_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")
    GOFORGE_WORKER_INTERVAL_SECONDS: int = int(os.getenv("GOFORGE_WORKER_INTERVAL_SECONDS", "900"))
    # A launch with a locked verdict is refreshed this rarely (keeps the database operation budget small)
    GOFORGE_SETTLED_INTERVAL_SECONDS: int = int(os.getenv("GOFORGE_SETTLED_INTERVAL_SECONDS", "3600"))
    GOFORGE_HOLDERS_LIST_INTERVAL_SECONDS: int = int(os.getenv("GOFORGE_HOLDERS_LIST_INTERVAL_SECONDS", "900"))
    GOFORGE_STALE_AFTER_SECONDS: int = int(os.getenv("GOFORGE_STALE_AFTER_SECONDS", "2700"))
    GOFORGE_PUBLIC_URL: str = os.getenv("GOFORGE_PUBLIC_URL", "https://epochlabs.run/goforge")
    GOFORGE_DEX_CHAIN: str = os.getenv("GOFORGE_DEX_CHAIN", "robinhood")
    GOFORGE_VERDICT_HOURS: int = int(os.getenv("GOFORGE_VERDICT_HOURS", "48"))
    GOFORGE_TARGET_MC_USD: float = float(os.getenv("GOFORGE_TARGET_MC_USD", "30000"))

    # GoForge Registry (community launch): one round per UTC day. Every number below is a default until the owner
    # confirms it (brief section 13); none of it is hardcoded in the logic.
    GF_ENABLED: bool = os.getenv("GF_ENABLED", "true").lower() in ("true", "1", "yes")
    GF_WORKER_INTERVAL_SECONDS: int = int(os.getenv("GF_WORKER_INTERVAL_SECONDS", "60"))
    GF_SUBMIT_OPEN_HOUR_UTC: int = int(os.getenv("GF_SUBMIT_OPEN_HOUR_UTC", "0"))
    GF_SUBMIT_CLOSE_HOUR_UTC: int = int(os.getenv("GF_SUBMIT_CLOSE_HOUR_UTC", "12"))
    GF_VOTE_CLOSE_HOUR_UTC: int = int(os.getenv("GF_VOTE_CLOSE_HOUR_UTC", "20"))
    GF_ANNOUNCE_MINUTE_UTC: int = int(os.getenv("GF_ANNOUNCE_MINUTE_UTC", "5"))
    GF_LAUNCH_WINDOW_HOURS: int = int(os.getenv("GF_LAUNCH_WINDOW_HOURS", "24"))
    GF_MAX_IDEAS_PER_DAY: int = int(os.getenv("GF_MAX_IDEAS_PER_DAY", "100"))
    GF_SUBMIT_FEE_EPC: float = float(os.getenv("GF_SUBMIT_FEE_EPC", "1000"))
    GF_VOTE_MIN_EPC: float = float(os.getenv("GF_VOTE_MIN_EPC", "1000"))
    GF_VOTE_MIN_WALLET_AGE_DAYS: int = int(os.getenv("GF_VOTE_MIN_WALLET_AGE_DAYS", "7"))
    GF_VOTE_CHANGES_PER_HOUR: int = int(os.getenv("GF_VOTE_CHANGES_PER_HOUR", "30"))
    GF_WIN_COOLDOWN_DAYS: int = int(os.getenv("GF_WIN_COOLDOWN_DAYS", "7"))
    GF_EPC_DECIMALS: int = int(os.getenv("GF_EPC_DECIMALS", "18"))
    GF_SIMILARITY_THRESHOLD: float = float(os.getenv("GF_SIMILARITY_THRESHOLD", "0.95"))
    @property
    def gf_team_wallets(self) -> frozenset[str]:
        extra = [a.strip().lower() for a in self.GF_TEAM_WALLETS.split(",") if a.strip()]
        return frozenset([self.GOLEM_WALLET.lower(), self.GOLEM_AGENT.lower(), self.EPOCH_LAUNCHER_OWNER.lower(), *extra])

    @property
    def gf_team_x_user_ids(self) -> frozenset[str]:
        return frozenset(a.strip() for a in self.GF_TEAM_X_USER_IDS.split(",") if a.strip())

    # Sessions, SIWE and X OAuth 2.0 (PKCE). Without GF_SESSION_SECRET login is refused, never "open".
    GF_SESSION_SECRET: str = os.getenv("GF_SESSION_SECRET", "")
    GF_SESSION_TTL_HOURS: int = int(os.getenv("GF_SESSION_TTL_HOURS", "168"))
    GF_SIWE_DOMAIN: str = os.getenv("GF_SIWE_DOMAIN", "epochlabs.run")
    GF_SIWE_URI: str = os.getenv("GF_SIWE_URI", "https://epochlabs.run/goforge")
    GF_FRONTEND_URL: str = os.getenv("GF_FRONTEND_URL", "https://epochlabs.run/goforge")
    GF_X_CLIENT_ID: str = os.getenv("GF_X_CLIENT_ID", "")
    GF_X_CLIENT_SECRET: str = os.getenv("GF_X_CLIENT_SECRET", "")
    GF_X_REDIRECT_URI: str = os.getenv("GF_X_REDIRECT_URI", "")
    # The team may not submit or vote: wallets and X user ids, comma separated (Golem's own wallets are always included)
    GF_TEAM_WALLETS: str = os.getenv("GF_TEAM_WALLETS", "")
    GF_TEAM_X_USER_IDS: str = os.getenv("GF_TEAM_X_USER_IDS", "")
    # Manual review and launch registration (admin API). Empty = the admin endpoints answer 403.
    GF_ADMIN_TOKEN: str = os.getenv("GF_ADMIN_TOKEN", "")
    # "manual": Golem schedules the launch, the team launches on Pons and registers the CA. "pons" is a stub.
    GF_LAUNCH_MODE: str = os.getenv("GF_LAUNCH_MODE", "manual").lower()
    GF_IMAGE_DIR: str = os.getenv("GF_IMAGE_DIR", "")
    GF_IMAGE_BASE_URL: str = os.getenv("GF_IMAGE_BASE_URL", "/api/goforge/images")
    # FeeSplitter factory and the keeper key that calls distribute() every GF_DISTRIBUTE_INTERVAL_HOURS (off when empty)
    GF_SPLITTER_FACTORY: str = os.getenv("GF_SPLITTER_FACTORY", "")
    GF_KEEPER_PRIVATE_KEY: str = os.getenv("GF_KEEPER_PRIVATE_KEY", "")
    GF_DISTRIBUTE_INTERVAL_HOURS: int = int(os.getenv("GF_DISTRIBUTE_INTERVAL_HOURS", "24"))
    # Link template for the live post, e.g. "https://pons.example/token/{ca}". Empty = the live post carries no Pons line.
    GF_PONS_URL_TEMPLATE: str = os.getenv("GF_PONS_URL_TEMPLATE", "")
    GF_EXTRA_STOCK_TICKERS: str = os.getenv("GF_EXTRA_STOCK_TICKERS", "")
    GF_EXTRA_BLOCKED_NAMES: str = os.getenv("GF_EXTRA_BLOCKED_NAMES", "")
    # Announcements on X: live only with TWITTER_AUTO_POST_ENABLED as well, otherwise dry runs
    # Longest post the X account can send (weighted). 280 for a standard account; X Premium allows far more.
    # Development only: submissions and votes are open at any hour. The round clock, scoring and the worker are untouched.
    GF_DEV_OPEN: bool = os.getenv("GF_DEV_OPEN", "false").lower() in ("true", "1", "yes")
    GF_X_MAX_CHARS: int = int(os.getenv("GF_X_MAX_CHARS", "280"))
    GF_X_POST_ENABLED: bool = os.getenv("GF_X_POST_ENABLED", "false").lower() in ("true", "1", "yes")

    # Meta Radar (narrative map). One clustering run per day at RADAR_RUN_HOUR_UTC:RADAR_RUN_MINUTE_UTC.
    RADAR_WORKER_ENABLED: bool = os.getenv("RADAR_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")
    RADAR_RUN_HOUR_UTC: int = int(os.getenv("RADAR_RUN_HOUR_UTC", "0"))
    RADAR_RUN_MINUTE_UTC: int = int(os.getenv("RADAR_RUN_MINUTE_UTC", "15"))
    RADAR_MIN_CLUSTER_SIZE: int = int(os.getenv("RADAR_MIN_CLUSTER_SIZE", "15"))
    # HDBSCAN noise above this share of tokens falls back to KMeans (k chosen by silhouette in 8..25)
    RADAR_MAX_NOISE_FRACTION: float = float(os.getenv("RADAR_MAX_NOISE_FRACTION", "0.5"))
    RADAR_MATCH_MAX_COSINE: float = float(os.getenv("RADAR_MATCH_MAX_COSINE", "0.25"))
    # Sample-size rules for any percentage shown: below MIN none, below NORMAL "low confidence"
    RADAR_MIN_RESOLVED: int = int(os.getenv("RADAR_MIN_RESOLVED", "20"))
    RADAR_NORMAL_RESOLVED: int = int(os.getenv("RADAR_NORMAL_RESOLVED", "50"))
    # 0 = the whole token history, otherwise only tokens launched within this many days
    RADAR_HISTORY_DAYS: int = int(os.getenv("RADAR_HISTORY_DAYS", "0"))
    # A 7d window needs this many resolved tokens before its survival rate is used (trend / saturation)
    RADAR_MIN_WINDOW_RESOLVED: int = int(os.getenv("RADAR_MIN_WINDOW_RESOLVED", "5"))
    RADAR_SATURATION_MIN_LAUNCHES: int = int(os.getenv("RADAR_SATURATION_MIN_LAUNCHES", "5"))
    RADAR_TREND_THRESHOLD_PCT: float = float(os.getenv("RADAR_TREND_THRESHOLD_PCT", "25"))
    RADAR_EXAMPLE_MIN_AGE_DAYS: int = int(os.getenv("RADAR_EXAMPLE_MIN_AGE_DAYS", "7"))

    # Twitter / X API v2 Credentials & Auto-Post Settings
    TWITTER_API_KEY: str = os.getenv("TWITTER_API_KEY", "")
    TWITTER_API_SECRET: str = os.getenv("TWITTER_API_SECRET", "")
    TWITTER_ACCESS_TOKEN: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
    TWITTER_ACCESS_TOKEN_SECRET: str = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
    TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")
    TWITTER_AUTO_POST_ENABLED: bool = os.getenv("TWITTER_AUTO_POST_ENABLED", "false").lower() in ("true", "1", "yes")
    TWITTER_POST_INTERVAL_HOURS: int = int(os.getenv("TWITTER_POST_INTERVAL_HOURS", "2"))
    TWITTER_MANAGED_BY_HANDLE: str = os.getenv("TWITTER_MANAGED_BY_HANDLE", "@EpochLabsHQ")
    USE_FULL_CA: bool = os.getenv("USE_FULL_CA", "false").lower() in ("true", "1", "yes")

    # Xiaomi MiMo LLM Settings
    MIMO_API_KEY: str = os.getenv("MIMO_API_KEY", "")
    MIMO_BASE_URL: str = os.getenv("MIMO_BASE_URL", "https://token-plan-sgp.xiaomimimo.com/v1")
    MIMO_MODEL: str = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
