import React from 'react';
import { RADAR_HERO } from '@/config/radarCopy';
import { RadarShowcase } from './RadarShowcase';
import type { RadarPayload, RadarPoint } from './types';
import { fmtCi, fmtPct, fmtRunTime } from './types';

const Stat: React.FC<{ label: string; value: React.ReactNode; sub?: string }> = ({ label, value, sub }) => (
  <div className="bg-[var(--panel)] px-3.5 py-3 min-w-0">
    <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--faint)] truncate">{label}</dt>
    <dd className="mt-1 font-mono tabular-nums text-[24px] md:text-[28px] leading-none font-semibold text-[var(--fg-hi)]">{value}</dd>
    {sub && <div className="mt-1 font-mono text-[10.5px] text-[var(--dim)] leading-tight whitespace-nowrap">{sub}</div>}
  </div>
);

export const RadarHero: React.FC<{ data: RadarPayload | null; points: RadarPoint[] }> = ({ data, points }) => (
  <section className="relative overflow-hidden border-b border-[var(--rule)]">
    <div aria-hidden className="pointer-events-none absolute inset-0 epochs-grid" />
    <div aria-hidden className="pointer-events-none absolute right-[-10%] top-[-20%] w-[60%] h-[140%] bg-[radial-gradient(closest-side,rgba(233,159,48,.16),transparent)]" />
    <div className="relative max-w-[1180px] mx-auto px-4 md:px-12 pt-10 pb-10 md:pt-14 md:pb-12 grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-8 lg:gap-12 items-center">
      <div className="min-w-0">
        <div className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[var(--banana)] flex items-center gap-3">
          <span className="w-8 h-px bg-[var(--banana)]" />
          Epoch Labs
        </div>
        <h1 className="font-sans font-semibold text-5xl md:text-7xl tracking-[-0.045em] leading-[0.95] mt-4 text-[var(--fg-hi)]">
          {RADAR_HERO.title}
        </h1>
        <p className="font-serif italic text-2xl md:text-[2rem] leading-[1.15] pb-1 text-[var(--banana)] mt-2">{RADAR_HERO.subtitle}</p>
        <p className="text-[var(--dim)] text-[15px] md:text-base max-w-[56ch] mt-4 leading-relaxed">{RADAR_HERO.intro}</p>

        {data && (
          <>
            <dl aria-label="Radar totals" className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden">
              <Stat label="Tokens mapped" value={data.totals.n_tokens.toLocaleString('en-US')} sub={data.totals.n_lore_missing > 0 ? `${data.totals.n_lore_missing.toLocaleString('en-US')} without lore` : undefined} />
              <Stat label="Narratives" value={data.totals.n_clusters} sub={data.unclustered ? `${data.unclustered.n_total.toLocaleString('en-US')} unclustered` : undefined} />
              <Stat
                label="Chain reached $30K"
                value={fmtPct(data.baseline.survival_rate, 1)}
                sub={`n = ${data.baseline.n_resolved.toLocaleString('en-US')}${data.baseline.ci ? `, 95% ${fmtCi(data.baseline.ci)}` : ''}`}
              />
            </dl>
            <p className="mt-3 font-mono text-[11.5px] text-[var(--faint)]">Updated daily. Last run {fmtRunTime(data.run_at)} UTC.</p>
          </>
        )}
      </div>
      {data && points.length > 0 && <RadarShowcase points={points} narratives={data.clusters} />}
    </div>
  </section>
);
