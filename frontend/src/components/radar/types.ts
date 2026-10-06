// GET /api/radar, /api/radar/points, /api/radar/{id}, /api/radar/{id}/examples.
// Mirrors backend/app/api/radar_endpoints.py. A rate the API withheld is null, never 0.

export type Confidence = 'insufficient' | 'low' | 'normal';
export type NarrativeStatus = 'rising' | 'cooling' | 'saturated' | 'steady' | 'low_data';

export interface Narrative {
  cluster_id: string;
  label: string;
  keywords: string[];
  label_source: string;
  n_total: number;
  n_resolved: number;
  reached_30k: number | null;
  survival_rate: number | null;
  ci: [number, number] | null;
  confidence: Confidence;
  note: string | null;
  lift: number | null;
  median_peak_mc: number | null;
  launches_7d: number;
  trend_pct: number | null;
  share_7d: number | null;
  survival_7d: number | null;
  survival_30d: number | null;
  saturation: boolean;
  status: NarrativeStatus;
  centroid: [number | null, number | null];
}

export interface RadarPayload {
  run_id: string;
  run_at: string | null;
  method: string;
  projection: string | null;
  params: { min_cluster_size: number | null; embedder: string | null };
  thresholds: { min_resolved: number; normal_resolved: number };
  totals: { n_tokens: number; n_clusters: number; n_lore_missing: number };
  baseline: { survival_rate: number | null; n_resolved: number; ci: [number, number] | null };
  unclustered: {
    n_total: number;
    n_resolved: number;
    survival_rate: number | null;
    ci: [number, number] | null;
    confidence: Confidence;
  } | null;
  clusters: Narrative[];
}

export interface RadarPoint {
  x: number;
  y: number;
  cluster_id: string;
  resolved: boolean;
  reached_30k: boolean | null;
}

export interface RadarPointsPayload {
  run_id: string;
  points: RadarPoint[];
}

export interface HistoryDay {
  date: string;
  launches: number;
  n_resolved_window: number;
  survival_window: number | null;
}

export interface RadarDetail {
  run_id: string;
  run_at: string | null;
  baseline: { survival_rate: number | null; n_resolved: number };
  cluster: Narrative;
  history: HistoryDay[];
}

export interface RadarExample {
  name: string;
  symbol: string;
  lore: string;
  outcome: 'reached_30k' | 'stalled';
  peak_mc: number | null;
  launched_at: string | null;
}

export interface RadarExamples {
  run_id: string;
  cluster_id: string;
  examples: RadarExample[];
}

export const UNCLUSTERED_ID = 'unclustered';

export function fmtPct(v: number | null | undefined, digits = 0): string {
  return v == null ? 'n/a' : `${(v * 100).toFixed(digits)}%`;
}

export function fmtCi(ci: [number, number] | null): string {
  return ci ? `${Math.round(ci[0] * 100)}-${Math.round(ci[1] * 100)}%` : '';
}

export function fmtLift(v: number | null | undefined): string {
  return v == null ? 'n/a' : `${v.toFixed(2)}x`;
}

export function fmtTrend(v: number | null | undefined): string {
  if (v == null) return 'n/a';
  const r = Math.round(v);
  return `${r > 0 ? '+' : ''}${r}%`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return 'n/a';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${Math.round(v)}`;
}

export function fmtRunTime(iso: string | null): string {
  if (!iso) return 'no run yet';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'no run yet';
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)}`;
}

// Hand-ordered hues so neighbouring ids never look alike; past the list, lightness alternates
const HUES = [12, 200, 85, 265, 170, 330, 48, 225, 140, 28, 300, 105];

/** Stable colour per narrative: the same id keeps its colour from one day to the next. */
export function clusterColor(clusterId: string): string {
  if (clusterId === UNCLUSTERED_ID) return '#8C7A60';
  const n = Math.max((parseInt(clusterId.replace(/\D/g, ''), 10) || 1) - 1, 0);
  const hue = HUES[n % HUES.length];
  const light = Math.floor(n / HUES.length) % 2 === 0 ? 64 : 52;
  return `hsl(${hue} 70% ${light}%)`;
}
