'use client';

import React, { useEffect, useState } from 'react';
import { fetchGoForgeHistory } from '@/hooks/useGoForge';
import { fmtUsd, TARGET_MC_USD, VERDICT_WINDOW_H, type GoForgeHistory } from './types';

const W = 320;
const H = 96;
const PAD = { l: 4, r: 4, t: 8, b: 14 };
const REFRESH_MS = 300_000;

/** Market cap over the first 48 hours with a dashed line at the $30K target. */
export const MiniChart: React.FC<{ id: string; launchedAt: string | null; live: boolean }> = ({ id, launchedAt, live }) => {
  const [hist, setHist] = useState<GoForgeHistory | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetchGoForgeHistory(id)
        .then((h) => { if (!cancelled) { setHist(h); setFailed(false); } })
        .catch(() => { if (!cancelled) setFailed(true); });
    load();
    // A settled launch has a closed window: read it once. A pending one grows every minute.
    const t = live ? setInterval(load, REFRESH_MS) : null;
    return () => { cancelled = true; if (t) clearInterval(t); };
  }, [id, live]);

  const target = hist?.target_mc_usd ?? TARGET_MC_USD;
  const windowH = hist?.window_hours ?? VERDICT_WINDOW_H;
  const points = hist?.points ?? [];
  const start = launchedAt ? new Date(launchedAt).getTime() : null;

  if (!hist || points.length < 2 || start === null) {
    return (
      <div
        role="img"
        aria-label="Market cap chart: waiting for snapshots"
        className="h-[96px] rounded-lg border border-dashed border-[var(--border)] flex items-center justify-center font-mono text-[11.5px] text-[var(--faint)]"
      >
        {failed && !hist ? 'Chart unavailable right now' : 'Collecting snapshots…'}
      </div>
    );
  }

  const maxMc = Math.max(target * 1.15, ...points.map((p) => p.mc_usd));
  const x = (ts: string) => PAD.l + ((new Date(ts).getTime() - start) / (windowH * 3600_000)) * (W - PAD.l - PAD.r);
  const y = (mc: number) => PAD.t + (1 - mc / maxMc) * (H - PAD.t - PAD.b);
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(p.mc_usd).toFixed(1)}`).join(' ');
  const last = points[points.length - 1];
  const yTarget = y(target);

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Market cap over the first ${windowH} hours, latest ${fmtUsd(last.mc_usd)}, target ${fmtUsd(target)}`}
        className="w-full h-auto block"
      >
        <line x1={PAD.l} x2={W - PAD.r} y1={yTarget} y2={yTarget} stroke="var(--banana)" strokeWidth="1" strokeDasharray="4 4" opacity=".75" />
        <text x={W - PAD.r} y={yTarget - 3} textAnchor="end" fontSize="9" fill="var(--banana)" fontFamily="monospace">{fmtUsd(target)}</text>
        <path d={line} fill="none" stroke="var(--fg-hi)" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(last.ts)} cy={y(last.mc_usd)} r="2.6" fill="var(--banana)" />
        <text x={PAD.l} y={H - 2} fontSize="9" fill="var(--faint)" fontFamily="monospace">0h</text>
        <text x={W - PAD.r} y={H - 2} textAnchor="end" fontSize="9" fill="var(--faint)" fontFamily="monospace">{windowH}h</text>
      </svg>
    </figure>
  );
};
