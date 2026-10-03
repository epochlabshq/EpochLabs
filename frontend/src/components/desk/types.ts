// GET /api/desk and /api/desk/trades/{id}. Mirrors backend/app/api/desk_endpoints.py.

export type DeskState = 'gated' | 'watching' | 'waiting' | 'entering' | 'in_position' | 'paused';

export interface AddressLink {
  address: string | null;
  url: string | null;
}

export interface DeskToken {
  name: string | null;
  symbol: string | null;
  address: string;
  url?: string;
  dexscreener_url?: string;
}

export interface WatchingRow {
  token: DeskToken;
  peak_mc: number | null;
  mc_now: number | null; // live from DexScreener
  mc_at: string | null;
  launched_at: string | null;
  holders: number | null;
  holders_sampled_at: string | null;
  survival: number | null;
  status: 'scoring' | 'below_threshold' | 'unscored' | 'awaiting_holders' | 'reached_tp' | 'excluded';
}

export interface WaitingSlot {
  slot: number;
  stage: 'liquidity_check' | 'sizing' | 'entering' | 'dropped';
  queued_at: string;
  dropped_reason?: string;
  dropped_at?: string;
}

export interface Signal {
  name: string;
  value: string;
  effect: '+' | '-' | '0';
}

export interface WhyCard {
  survival: number;
  threshold: number;
  top_signals: Signal[];
  model_run_id: number;
  proven_floor: number;
  size_eth: number;
  size_rule: string;
  exit_plan: { take_profit_mc_usd: number; stop_loss_mc_usd: number; max_hold_h: number };
  decided_at: string;
}

export interface Pnl {
  eth: number | null;
  pct: number | null;
}

export interface Leg {
  at: string;
  price: number | null;
  tx: string;
  tx_url: string;
}

export interface OpenTrade {
  id: string;
  token: DeskToken;
  entry: Leg & { size_eth: number };
  mark_price: number | null;
  pnl: Pnl;
  incomplete: boolean;
  why: WhyCard | null;
  why_sha256: string | null;
  why_verified: boolean;
}

export interface ClosedTrade {
  id: string;
  token: DeskToken;
  entry: Leg & { size_eth: number };
  exit: Leg & { proceeds_eth: number };
  duration_s: number;
  pnl: Pnl;
  exit_reason: 'take_profit' | 'stop_loss' | 'max_hold' | null;
  reached_30k: boolean | null; // from the token's 48h label; null while unlabeled
  epc_burned: number | null;
  incomplete: boolean;
  why_sha256: string | null;
  why_verified: boolean;
  why?: WhyCard | null;
}

export type TradeDetail = ({ status: 'open' } & OpenTrade) | ({ status: 'closed' } & ClosedTrade);

export interface DeskPayload {
  state: DeskState;
  blocked_by: string | null;
  last_decision_at: string | null;
  heartbeat: { last_decision_at: string | null; age_s: number | null; stale: boolean; warn_after_s: number };
  threshold: number;
  model: { run_id: number; proven_floor: number } | null;
  wallet: AddressLink & { eth: number | null; start_eth: number };
  agent: AddressLink;
  pnl: Pnl & { realized_eth: number; complete: boolean; wins: number; losses: number };
  epc_burned: { amount: number; amount_wei: string; usd: number | null; burn_address: AddressLink | null };
  watching: WatchingRow[];
  watching_not_onchain: number; // feed rows with no contract on Robinhood Chain: counted, not shown
  watching_no_price: number; // on Robinhood Chain but no DexScreener pair
  watching_below_min: number; // market cap fell back under the $10K bar
  watch_min_mc_usd: number;
  take_profit_mc_usd: number;
  waiting: WaitingSlot[];
  dropped_revealed: { slot: number; token: DeskToken; survival: number; dropped_reason: string; dropped_at: string }[];
  open: OpenTrade[];
  closed: ClosedTrade[];
  closed_total: number;
  generated_at: string;
}

// WS events on /stream
export type DeskEvent =
  | { desk_state: { state: DeskState; previous: DeskState | null; blocked_by: string | null; changed_at: string } }
  | { desk_waiting: { waiting: WaitingSlot[] } }
  | { desk_open: TradeDetail }
  | { desk_close: TradeDetail };

// ---- Formatting (every signed number carries + or −, so meaning never depends on color alone) ----

const MINUS = '−';

export const signed = (v: number, digits: number) => {
  const s = Math.abs(v).toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return `${v < 0 ? MINUS : '+'}${s}`;
};

export const fmtEth = (v: number | null, digits = 4) => (v === null ? '—' : v.toFixed(digits));
export const fmtSignedEth = (v: number | null, digits = 4) => (v === null ? '—' : signed(v, digits));
export const fmtSignedPct = (v: number | null, digits = 1) => (v === null ? '—' : `${signed(v, digits)}%`);

export const fmtPrice = (v: number | null) => {
  if (v === null) return '—';
  if (v === 0) return '0';
  return v >= 0.001 ? v.toFixed(6) : v.toExponential(3);
};

export const fmtUsdCompact = (v: number | null) => {
  if (v === null) return '—';
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return `$${v.toFixed(0)}`;
};

export const fmtCount = (v: number | null) => {
  if (v === null) return '—';
  return v >= 1000 ? `${(v / 1000).toFixed(1).replace(/\.0$/, '')}K` : String(v);
};

export const fmtAmount = (v: number) =>
  v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(v < 10 ? 2 : 0);

export const fmtDuration = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
};

export const ageSince = (iso: string | null, now: number) =>
  iso ? fmtDuration((now - new Date(iso).getTime()) / 1000) : '—';

export const pnlTone = (v: number | null) =>
  v === null || v === 0 ? 'text-[var(--fg)]' : v > 0 ? 'text-[var(--live)]' : 'text-[var(--stall)]';

export const tokenLabel = (t: DeskToken) => t.symbol || t.name || `${t.address.slice(0, 6)}…${t.address.slice(-4)}`;

export const EXIT_REASON_LABEL: Record<string, string> = {
  take_profit: 'Take-profit',
  stop_loss: 'Stop-loss',
  max_hold: 'Time limit',
};

export const STAGE_LABEL: Record<WaitingSlot['stage'], string> = {
  liquidity_check: 'waiting for liquidity check',
  sizing: 'sizing the position',
  entering: 'Entering…',
  dropped: 'Dropped',
};
