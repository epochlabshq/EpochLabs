'use client';

import React, { useEffect, useState } from 'react';
import { fetchRadarExamples } from '@/hooks/useRadar';
import { Radar3D, type RadarPin } from './Radar3D';
import type { Narrative, RadarExample, RadarPoint } from './types';
import { fmtUsd } from './types';

interface Shown extends RadarExample {
  narrative: string;
  clusterId: string;
}

const CARD_SECONDS = 4.5;

interface Props {
  points: RadarPoint[];
  narratives: Narrative[];
}

/**
 * The 3D radar plus a rotating card of older tokens. Only resolved tokens older than 7 days that passed the
 * lore filter come back from the examples endpoint, so no live token is ever named here.
 */
export const RadarShowcase: React.FC<Props> = ({ points, narratives }) => {
  const [shown, setShown] = useState<Shown[]>([]);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    const top = [...narratives].sort((a, b) => b.n_resolved - a.n_resolved).slice(0, 4);
    Promise.all(
      top.map((n) =>
        fetchRadarExamples(n.cluster_id, ctrl.signal)
          .then((r) => r.examples.slice(0, 2).map((e): Shown => ({ ...e, narrative: n.label, clusterId: n.cluster_id })))
          .catch(() => [] as Shown[]),
      ),
    ).then((lists) => {
      if (ctrl.signal.aborted) return;
      // Interleave narratives so consecutive cards differ
      const out: Shown[] = [];
      for (let i = 0; i < 2; i++) lists.forEach((l) => l[i] && out.push(l[i]));
      setShown(out);
    });
    return () => ctrl.abort();
  }, [narratives]);

  useEffect(() => {
    if (shown.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % shown.length), CARD_SECONDS * 1000);
    return () => clearInterval(t);
  }, [shown.length]);

  const card = shown[idx % (shown.length || 1)];
  // One pin per narrative first, then a second round, up to six: symbols only, from older resolved tokens
  const pins: RadarPin[] = shown.slice(0, 6).map((e) => ({ clusterId: e.clusterId, label: e.symbol || e.name, reached: e.outcome === 'reached_30k' }));

  return (
    <figure className="relative">
      <div aria-hidden className="pointer-events-none absolute -inset-10 bg-[radial-gradient(55%_50%_at_50%_50%,rgba(255,176,60,.07),transparent)]" />
      <Radar3D points={points} pins={pins} className="relative w-full h-[340px] md:h-[440px] -translate-y-[10%]" />
      {card && (
        <div
          key={`${card.clusterId}-${card.name}-${idx}`}
          aria-live="polite"
          className="absolute left-0 right-0 bottom-0 sm:left-auto sm:-right-[30px] sm:w-[320px] overflow-hidden rounded-lg border border-[rgba(255,196,102,.22)] bg-[linear-gradient(135deg,rgba(54,36,14,.78),rgba(24,15,6,.7))] backdrop-blur-xl shadow-[0_14px_40px_-12px_rgba(0,0,0,.7),inset_0_1px_0_rgba(255,226,170,.08)] pl-5 pr-4 py-3.5 [animation:radarCardIn_.6s_ease_both] motion-reduce:animate-none"
        >
          <span aria-hidden className={`absolute inset-y-0 left-0 w-[3px] ${card.outcome === 'reached_30k' ? 'bg-[#ffd36b]' : 'bg-[#8a5a1c]'}`} />
          <div className="flex items-baseline justify-between gap-4">
            <span className="min-w-0 truncate text-[15px] font-medium tracking-tight text-[var(--fg-hi)]">
              {card.name}
              <span className="ml-2 font-mono text-[11px] font-normal text-[var(--faint)]">{card.symbol}</span>
            </span>
            <span className={`shrink-0 font-mono text-[10.5px] uppercase tracking-[0.14em] ${card.outcome === 'reached_30k' ? 'text-[#ffd36b]' : 'text-[var(--dim)]'}`}>
              {card.outcome === 'reached_30k' ? 'Reached $30K' : 'Stalled'}
            </span>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-4 text-[12px] text-[var(--dim)]">
            <span className="min-w-0 truncate">{card.narrative}</span>
            <span className="shrink-0 font-mono tabular-nums">Peak {fmtUsd(card.peak_mc)}</span>
          </div>
        </div>
      )}
    </figure>
  );
};
