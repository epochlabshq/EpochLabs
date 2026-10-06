// GoForge Registry API. Mirrors backend/app/api/gf_endpoints.py and goforge_endpoints.py.
// A number the backend could not read is null, never 0: the UI shows a dash or "pending".

export type Phase = 'submit' | 'vote' | 'scoring' | 'announced';
export type Verdict = 'pending' | 'reached_30k' | 'stalled';
export type IdeaStatus = 'pending_review' | 'approved' | 'rejected';
export type NextLabel = 'submit_closes' | 'vote_closes' | 'announcement' | 'submit_opens';

export interface PublicIdea {
  idea_id: string;
  name: string;
  ticker: string;
  lore: string;
  image_url: string;
  creator_handle: string | null;
  votes: number | null;
  submitted_at: string | null;
}

export interface OwnIdea extends Omit<PublicIdea, 'votes'> {
  round_date: string;
  status: IdeaStatus;
  reject_reason: string | null;
}

export interface Winner extends PublicIdea {
  scores: { credibility: number; golem: number; vote: number; final: number };
}

export interface RoundTotals {
  launches: number;
  epc_burned_from_fees: number;
  epc_burned_from_submit_fees: number;
  fees_paid_to_creators_eth: number;
  ideas_submitted: number;
}

export interface Rules {
  max_ideas_per_day: number;
  submit_fee_epc: number;
  vote_min_epc: number;
  vote_min_wallet_age_days: number;
  vote_changes_per_hour: number;
  win_cooldown_days: number;
  weights: { credibility: number; golem: number; vote: number };
  chain_id: number;
  epc_token: string;
  burn_address: string;
  similarity_threshold: number;
  login_configured: boolean;
  x_login_configured: boolean;
}

export interface Schedule {
  submit_opens_hour_utc: number;
  submit_closes_hour_utc: number;
  vote_closes_hour_utc: number;
  announce_minute_utc: number;
  launch_window_hours: number;
}

export interface LastRound {
  round_date: string;
  status: string;
  no_launch_reason: string | null;
  winner: PublicIdea | null;
  n_ideas: number | null;
  n_votes: number | null;
}

export interface RoundPayload {
  round_date: string;
  phase: Phase;
  status: string;
  now: string;
  next: { label: NextLabel; at: string };
  slots: { used: number; max: number };
  pool_visible: boolean;
  ideas: PublicIdea[];
  n_ideas_approved: number | null;
  n_votes: number | null;
  winner: Winner | null;
  no_launch_reason: string | null;
  scoreboard_available: boolean;
  last_round: LastRound | null;
  totals: RoundTotals;
  rules: Rules;
  schedule: Schedule;
}

export interface Me {
  wallet: string | null;
  x: { handle: string | null; verified: boolean | null; followers: number | null } | null;
  is_team: boolean;
  my_vote: { round_date: string; idea_id: string } | null;
  submitted_today: boolean;
}

export interface ScoreRow {
  rank: number;
  idea_id: string;
  name: string;
  ticker: string;
  x_handle: string | null;
  credibility: number;
  golem: number;
  vote: number;
  final: number;
  votes: number;
  image_url: string;
  winner: boolean;
}

export interface Scoreboard {
  round_date: string;
  status: string;
  rows: ScoreRow[];
  scoreboard_sha256: string | null;
  formula: string;
  n_ideas: number | null;
  n_votes: number | null;
  no_launch_reason: string | null;
  announced_at: string | null;
}

export interface Problem {
  field: string;
  code: string;
  message: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  problems?: Problem[];
  reason?: string;
}

// ---- Forged tokens (archive) ----

export interface Distribution {
  tx_hash: string;
  tx_url: string;
  at: string;
  creator_eth: number;
  burn_eth: number;
  epc_burned: number;
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
  verdict: Verdict;
  verdict_at: string | null;
  stale: boolean;
  stale_age_s: number | null;
  market_updated_at: string | null;
  links: { blockscout_token: string; launch_tx: string; dexscreener: string };
  share_text: string;
  // registry facts (community launches)
  community?: boolean;
  idea_id?: string;
  image_url?: string;
  creator_handle?: string | null;
  round_date?: string;
  splitter_address?: string | null;
  fees_to_creator_eth?: number;
  epc_burned_from_fees?: number;
  distributions?: Distribution[];
  scoreboard_url?: string;
}

export interface LaunchesPayload {
  launches: Launch[];
  totals: RoundTotals;
  generated_at: string;
}

// WS events on /stream
export type GfEvent =
  | { gf_vote: { round_date: string; votes: Record<string, number> } }
  | { gf_phase: { round_date: string; phase: Phase } }
  | { gf_winner: { round_date: string; idea_id: string; name: string; ticker: string } }
  | { gf_launch: { idea_id: string; ca: string } }
  | { gf_verdict: { idea_id: string; verdict: Verdict } };

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

export const fmtEth = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${Number(v.toFixed(v >= 1 ? 2 : 4))} ETH`;

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

// ---- Price history of a launched token (first 48 hours) ----

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
