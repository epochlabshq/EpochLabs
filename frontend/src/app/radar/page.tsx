'use client';

import React, { useCallback, useState } from 'react';
import { HeaderBar } from '@/components/layout/HeaderBar';
import { FooterBar } from '@/components/layout/FooterBar';
import { NarrativeDrawer } from '@/components/radar/NarrativeDrawer';
import { NarrativeMap } from '@/components/radar/NarrativeMap';
import { NarrativeTable } from '@/components/radar/NarrativeTable';
import { RadarHero } from '@/components/radar/RadarHero';
import { Methodology, RadarFooter, ReadingCards } from '@/components/radar/RadarFooter';
import { fmtCi, fmtPct, fmtRunTime } from '@/components/radar/types';
import { NO_DATA, RADAR_EMPTY, RADAR_ERROR } from '@/config/radarCopy';
import { useRadar } from '@/hooks/useRadar';

export default function RadarPage() {
  const { state, data, points } = useRadar();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const close = useCallback(() => setSelectedId(null), []);

  const un = data?.unclustered;

  return (
    <div className="wrap min-h-screen flex flex-col overflow-x-clip">
      <HeaderBar phaseText={data ? `radar · run ${fmtRunTime(data.run_at).slice(0, 10)}` : 'radar'} />
      <main className="flex-1">
        <RadarHero data={data} points={points?.points ?? []} />

        <div className="max-w-[1180px] mx-auto w-full px-4 md:px-12 py-8 md:py-12 space-y-10 md:space-y-12">
          {state === 'loading' && (
            <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">Loading Radar…</div>
          )}
          {state === 'empty' && (
            <div role="status" data-testid="empty-state" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">{RADAR_EMPTY}</div>
          )}
          {state === 'error' && (
            <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">{RADAR_ERROR}</div>
          )}

          {state === 'ready' && data && (
            <>
              <section aria-labelledby="map-h" className="min-w-0">
                <h2 id="map-h" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">Narrative map</h2>
                <p className="mt-1 mb-4 text-[14px] text-[var(--dim)] max-w-[62ch]">Tokens with similar lore sit close together. Hover a dot or pick a narrative to isolate it, click to open its detail.</p>
                {points && points.points.length > 0 ? (
                  <NarrativeMap points={points.points} narratives={data.clusters} selectedId={selectedId} onSelect={setSelectedId} />
                ) : (
                  <div className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">Map points unavailable. The table below is unaffected.</div>
                )}
              </section>

              <section aria-labelledby="table-h" className="min-w-0">
                <h2 id="table-h" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">
                  Narratives
                  <span className="ml-2 font-mono text-[13px] font-normal text-[var(--faint)]">{data.clusters.length}</span>
                </h2>
                <p className="mt-1 mb-4 text-[14px] text-[var(--dim)] max-w-[70ch]">
                  Sorted by resolved sample size, not by rate, so thin data never sits on top. Under {data.thresholds.min_resolved} resolved tokens no percentage is shown.
                </p>
                {data.clusters.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">No narrative has formed yet.</div>
                ) : (
                  <NarrativeTable narratives={data.clusters} baseline={data.baseline.survival_rate} baselineRow={data.baseline} selectedId={selectedId} onSelect={setSelectedId} />
                )}
                {un && (
                  <p className="mt-3 text-[13px] text-[var(--dim)]" data-testid="unclustered">
                    Unclustered: {un.n_total.toLocaleString('en-US')} tokens fit no narrative. {' '}
                    {un.survival_rate != null ? `${fmtPct(un.survival_rate)} reached $30K (95% ${fmtCi(un.ci)}, n = ${un.n_resolved})` : NO_DATA(un.n_resolved)}
                  </p>
                )}
              </section>

              <ReadingCards />
              <Methodology data={data} />
            </>
          )}
        </div>

        <RadarFooter />
      </main>
      <FooterBar />
      <NarrativeDrawer clusterId={selectedId} baseline={data?.baseline.survival_rate ?? null} onClose={close} />
    </div>
  );
}
