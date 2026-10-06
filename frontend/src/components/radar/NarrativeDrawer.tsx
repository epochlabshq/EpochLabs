'use client';

import React, { useEffect, useRef, useState } from 'react';
import { fetchRadarDetail, fetchRadarExamples } from '@/hooks/useRadar';
import { NO_DATA, SATURATION_WARNING, STATUS_HINT, STATUS_LABEL } from '@/config/radarCopy';
import { CiBar } from './CiBar';
import type { HistoryDay, RadarDetail, RadarExample } from './types';
import { clusterColor, fmtCi, fmtLift, fmtPct, fmtTrend, fmtUsd } from './types';

const HistoryChart: React.FC<{ history: HistoryDay[]; color: string }> = ({ history, color }) => {
  const W = 560, H = 150, L = 30, R = 30, T = 10, B = 22;
  const iw = W - L - R, ih = H - T - B;
  const maxL = Math.max(1, ...history.map((d) => d.launches));
  const bw = iw / history.length;
  const pts = history
    .map((d, i) => (d.survival_window == null ? null : { x: L + bw * (i + 0.5), y: T + ih * (1 - d.survival_window), d }))
    .filter((p): p is NonNullable<typeof p> => p !== null);
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const total = history.reduce((s, d) => s + d.launches, 0);
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={`Last 30 days: ${total} launches. Bars are launches per day, the line is the 7-day rolling survival rate where there are enough resolved tokens.`}>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={T + ih * (1 - t)} y2={T + ih * (1 - t)} stroke="var(--rule)" />
            <text x={W - R + 4} y={T + ih * (1 - t) + 3} fontSize="9" fill="var(--faint)" fontFamily="monospace">{Math.round(t * 100)}%</text>
          </g>
        ))}
        <text x={L - 4} y={T + 8} textAnchor="end" fontSize="9" fill="var(--faint)" fontFamily="monospace">{maxL}</text>
        {history.map((d, i) => (
          <rect key={d.date} x={L + bw * i + 1} width={Math.max(bw - 2, 1)} y={T + ih * (1 - d.launches / maxL)} height={ih * (d.launches / maxL)} fill={color} opacity="0.45">
            <title>{`${d.date}: ${d.launches} launches`}</title>
          </rect>
        ))}
        {line && <path d={line} fill="none" stroke="var(--fg-hi)" strokeWidth="1.6" />}
        {pts.map((p) => (
          <circle key={p.d.date} cx={p.x} cy={p.y} r="2.2" fill="var(--fg-hi)">
            <title>{`${p.d.date}: ${fmtPct(p.d.survival_window)} reached $30K (n = ${p.d.n_resolved_window}, 7-day window)`}</title>
          </circle>
        ))}
        <text x={L} y={H - 6} fontSize="9" fill="var(--faint)" fontFamily="monospace">{history[0]?.date.slice(5)}</text>
        <text x={W - R} y={H - 6} textAnchor="end" fontSize="9" fill="var(--faint)" fontFamily="monospace">{history[history.length - 1]?.date.slice(5)}</text>
      </svg>
      <figcaption className="font-mono text-[10.5px] text-[var(--dim)] mt-1">
        Bars: launches per day (max {maxL}). Line: survival rate of resolved tokens launched in the trailing 7 days; a gap means fewer than 5 resolved tokens.
      </figcaption>
    </figure>
  );
};

interface BodyProps {
  clusterId: string;
  baseline: number | null;
  onClose: () => void;
}

const DrawerBody: React.FC<BodyProps> = ({ clusterId, baseline, onClose }) => {
  const [detail, setDetail] = useState<RadarDetail | null>(null);
  const [examples, setExamples] = useState<RadarExample[] | null>(null);
  const [failed, setFailed] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Mounted per narrative (see NarrativeDrawer), so state starts empty and never needs resetting here
  useEffect(() => {
    const ctrl = new AbortController();
    fetchRadarDetail(clusterId, ctrl.signal).then(setDetail).catch((e) => { if (e.name !== 'AbortError') setFailed(true); });
    fetchRadarExamples(clusterId, ctrl.signal).then((r) => setExamples(r.examples)).catch(() => { if (!ctrl.signal.aborted) setExamples([]); });
    closeRef.current?.focus();
    return () => ctrl.abort();
  }, [clusterId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clusterId, onClose]);

  const n = detail?.cluster;
  const color = clusterColor(clusterId);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Narrative detail">
      <button type="button" aria-label="Close detail" tabIndex={-1} onClick={onClose} className="absolute inset-0 bg-black/60 cursor-default" />
      <aside className="relative w-full sm:max-w-[520px] h-full overflow-y-auto overflow-x-hidden bg-[var(--panel)] border-l border-[var(--border-strong)] p-5 md:p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.2em] text-[var(--faint)]">Narrative {clusterId}</div>
            <h2 className="mt-1 font-sans font-semibold text-2xl tracking-tight text-[var(--fg-hi)] break-words">
              <span aria-hidden className="inline-block w-3 h-3 rounded-full mr-2" style={{ background: color }} />
              {n?.label ?? '…'}
            </h2>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} className="shrink-0 rounded-md border border-[var(--border-strong)] px-3 py-1.5 font-mono text-[12px] text-[var(--fg)] hover:border-[var(--banana)]">
            Close
          </button>
        </div>

        {failed && <p role="status" className="mt-6 font-mono text-[12.5px] text-[var(--stall)]">Narrative detail unavailable.</p>}
        {!n && !failed && <p role="status" className="mt-6 font-mono text-[12.5px] text-[var(--dim)]">Loading…</p>}

        {n && detail && (
          <div className="mt-5 space-y-6">
            {n.keywords.length > 0 && (
              <ul aria-label="Keywords" className="flex flex-wrap gap-1.5">
                {n.keywords.map((k) => (
                  <li key={k} className="rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 font-mono text-[11px] text-[var(--fg)]">{k}</li>
                ))}
              </ul>
            )}

            {n.saturation && (
              <div role="alert" className="rounded-lg border border-[var(--stall)] bg-[var(--stall-glow)] px-4 py-3 text-[13.5px] text-[var(--fg-hi)]">
                <strong className="font-mono text-[11px] uppercase tracking-wider text-[var(--stall)]">Saturated · </strong>
                {SATURATION_WARNING}
              </div>
            )}

            <section aria-labelledby="rate-h">
              <h3 id="rate-h" className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)]">Reached $30K within 48h</h3>
              {n.survival_rate == null ? (
                <p className="mt-2 text-[15px] text-[var(--dim)]">{NO_DATA(n.n_resolved)}</p>
              ) : (
                <>
                  <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="font-mono tabular-nums text-4xl font-semibold text-[var(--fg-hi)]">{fmtPct(n.survival_rate)}</span>
                    <span className="font-mono text-[13px] text-[var(--dim)]">95% interval {fmtCi(n.ci)}</span>
                    {n.confidence === 'low' && <span className="rounded border border-dashed border-[var(--banana)] px-1.5 font-mono text-[11px] text-[var(--banana)]">low confidence</span>}
                  </div>
                  <div className="mt-3"><CiBar rate={n.survival_rate} ci={n.ci} baseline={baseline} confidence={n.confidence} /></div>
                  <p className="mt-2 font-mono text-[11px] text-[var(--dim)]">Dashed line: chain baseline {fmtPct(baseline, 1)}.</p>
                </>
              )}
              <dl className="mt-4 grid grid-cols-2 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-lg overflow-hidden font-mono">
                {[
                  ['Resolved (n)', String(n.n_resolved)],
                  ['Tokens', String(n.n_total)],
                  ['vs chain', fmtLift(n.lift)],
                  ['Median peak MC', fmtUsd(n.median_peak_mc)],
                  ['Launches 7d', `${n.launches_7d} (${fmtTrend(n.trend_pct)})`],
                  ['Status', STATUS_LABEL[n.status] ?? n.status],
                ].map(([k, v]) => (
                  <div key={k} className="bg-[var(--panel)] px-3 py-2">
                    <dt className="text-[9.5px] uppercase tracking-wider text-[var(--faint)]">{k}</dt>
                    <dd className="text-[14px] tabular-nums text-[var(--fg-hi)]">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 text-[12.5px] text-[var(--dim)]">{STATUS_HINT[n.status]}</p>
            </section>

            <section aria-labelledby="hist-h">
              <h3 id="hist-h" className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)] mb-2">Last 30 days</h3>
              <HistoryChart history={detail.history} color={color} />
            </section>

            <section aria-labelledby="ex-h">
              <h3 id="ex-h" className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)] mb-2">Older examples and how they ended</h3>
              {examples === null && <p className="font-mono text-[12px] text-[var(--dim)]">Loading…</p>}
              {examples !== null && examples.length === 0 && (
                <p className="text-[13px] text-[var(--dim)]">No examples to show yet. Only resolved tokens older than 7 days are listed.</p>
              )}
              <ul className="space-y-2">
                {examples?.map((e, i) => (
                  <li key={`${e.name}-${i}`} className="rounded-lg border border-[var(--rule)] bg-[var(--panel2)] px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13.5px] font-medium text-[var(--fg-hi)] break-words min-w-0">{e.name} <span className="font-mono text-[11px] text-[var(--faint)]">{e.symbol}</span></span>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${e.outcome === 'reached_30k' ? 'border-[var(--live)] text-[var(--live)]' : 'border-[var(--stall)] text-[var(--stall)]'}`}>
                        {e.outcome === 'reached_30k' ? 'Reached $30K' : 'Stalled'}
                      </span>
                    </div>
                    <p className="mt-1 text-[12.5px] text-[var(--dim)] leading-snug break-words">{e.lore}</p>
                    <p className="mt-1 font-mono text-[10.5px] text-[var(--faint)]">Peak MC in 48h {fmtUsd(e.peak_mc)}</p>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
};

interface Props {
  clusterId: string | null;
  baseline: number | null;
  onClose: () => void;
}

export const NarrativeDrawer: React.FC<Props> = ({ clusterId, baseline, onClose }) =>
  clusterId ? <DrawerBody key={clusterId} clusterId={clusterId} baseline={baseline} onClose={onClose} /> : null;
