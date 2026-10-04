// GET /api/goforge and /api/goforge/{id}/history. Mirrors backend/app/api/goforge_endpoints.py.
// A number the backend could not read is null, never 0: the UI shows a dash or "pending".

export type GateStatus = 'locked' | 'ready' | 'forging' | 'cooldown';
export type Verdict = 'pending' | 'reached_30k' | 'stalled';

export interface GateCondition {
  key: 'trading_proof' | 'track_record' | 'model_fix' | 'cooldown' | 'capital' | string;
  label: string;
  passed: boolean;
  current?: number | null;
  target?: number;
}

export interface Gate {
  status: GateStatus;
  blocked_by: string[];
  conditions: GateCondition[];
  next_launch_possible_at: string | null;
}

export interface GoForgeTotals {
  launches: number;
  reached_30k: number;
  stalled: number;
  pending: number;
  fees_usd: number | null;
  epc_burned: number | null;
}

export interface WhyInfo {
  window: string | null;
  window_reason: string | null;
  lore_summary: string | null;
  model_run_id: string | number | null;
  why_hash: string | null;
}

export interface HookRules {
  hook_address: string | null;
  lp_lock_address: string | null;
  lp_lock_until: string | null;
  anti_snipe_blocks: number | null;
  wallet_cap_pct: number | null;
  wallet_cap_minutes: number | null;
  min_liquidity_usd: number | null;
}

export interface Launch {
  id: string;
  ca: string;
  name: string | null;
  symbol: string | null;
  launched_at: string | null;
  hours_since_launch: number | null;
  seconds_to_verdict: number | null;
  price_usd: number | null;
  mc_usd: number | null;
  peak_mc_usd: number | null;
  liquidity_usd: number | null;
  volume_24h_usd: number | null;
  holders: number | null;
  top10_holder_pct: number | null;
  verdict: Verdict;
  verdict_at: string | null;
  fees_usd: number | null;
  epc_burned: number | null;
  stale: boolean;
  stale_age_s: number | null;
  market_updated_at: string | null;
  links: { blockscout_token: string; launch_tx: string; dexscreener: string };
  why: WhyInfo;
  rules: HookRules;
  rules_pending: boolean;
  fee_router_address: string | null;
  share_text: string;
}

export interface GoForgePayload {
  gate: Gate | null; // null when the Desk data the gate depends on could not be read
  totals: GoForgeTotals;
  launches: Launch[];
  generated_at: string;
}

export interface HistoryPoint {
  ts: string;
  mc_usd: number;
  price_usd: number | null;
}

export interface GoForgeHistory {
  target_mc_usd: number;
  window_hours: number;
  points: HistoryPoint[];
}

// WS events on /stream
export type GoForgeEvent =
  | { goforge_update: Launch }
  | { goforge_verdict: { id: string; verdict: Verdict; verdict_at: string | null; launch: Launch } }
  | { goforge_burn: { id: string; epc_burned: number | null; delta: number } };

// ---- Formatting: null is a dash, never 0 / NaN / "undefined" ----

export const fmtUsd = (v: number | null | undefined) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return `$${v.toFixed(0)}`;
};

export const fmtPriceUsd = (v: number | null | undefined) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v === 0) return '$0';
  if (v >= 0.01) return `$${v.toFixed(4)}`;
  // Small prices keep three significant digits without exponent notation: 0.00014 -> $0.000140
  return `$${v.toFixed(Math.min(12, 2 - Math.floor(Math.log10(v))))}`;
};

export const fmtInt = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1).replace(/\.0$/, '')}K` : String(v);

export const fmtTokens = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(v < 10 ? 2 : 0);

export const shortAddr = (a: string | null | undefined) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—');

export const fmtCountdown = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
};

export const fmtAgo = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
};

// Always UTC, so the record reads the same for everyone
export const fmtDateTime = (iso: string | null) =>
  iso ? `${new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })} UTC` : '—';

/** Seconds left to hour 48, counted from the launch time so the countdown stays right between refreshes. */
export const secondsToVerdict = (launchedAt: string | null, windowHours: number, now: number): number | null =>
  launchedAt ? Math.max(0, Math.floor((new Date(launchedAt).getTime() + windowHours * 3600_000 - now) / 1000)) : null;

export const VERDICT_WINDOW_H = 48;
export const TARGET_MC_USD = 30_000;
