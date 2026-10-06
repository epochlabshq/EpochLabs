'use client';

import React from 'react';
import { GOFORGE_HERO } from '@/config/goforgeCopy';
import { NEXT_LABEL, PHASE_LABEL, secondsUntil } from '@/lib/gf';
import { useNow } from '@/components/desk/DeskUi';
import { ComingSoonPill } from './ComingSoon';
import { useEmileStore } from '@/store/useEmileStore';
import { GolemHero } from './GolemHero';
import { fmtCountdown, fmtEth, fmtTokens, type RoundPayload } from './types';

const PHASE_TONE: Record<string, string> = {
  submit: 'border-[var(--live)] text-[var(--live)] bg-[var(--live-glow)]',
  vote: 'border-[var(--banana)] text-[var(--banana)] bg-[var(--banana-glow)]',
  scoring: 'border-[var(--banana)] text-[var(--banana)] bg-transparent',
  announced: 'border-[var(--border-strong)] text-[var(--fg)] bg-transparent',
};

const Stat: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="bg-[var(--panel)] px-4 py-3 min-w-0">
    <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)] truncate">{label}</dt>
    <dd className="mt-1 font-mono tabular-nums text-[15px] sm:text-[17px] md:text-[19px] font-semibold text-[var(--fg-hi)] truncate">{value}</dd>
  </div>
);

export const GfHero: React.FC<{ round: RoundPayload | null; soon?: boolean }> = ({ round, soon = false }) => {
  const now = useNow(1000) + useEmileStore((s) => s.gfClockOffsetMs);
  const left = round ? secondsUntil(round.next.at, now) : null;
  const t = round?.totals;
  const burned = t ? t.epc_burned_from_fees + t.epc_burned_from_submit_fees : null;

  return (
    <section className="relative overflow-hidden border-b border-[var(--rule)]">
      <div aria-hidden className="pointer-events-none absolute inset-0 epochs-grid" />
      <div aria-hidden className="pointer-events-none absolute right-[-10%] top-[-10%] w-[70%] h-[120%] bg-[radial-gradient(closest-side,rgba(233,159,48,.22),transparent)]" />
      <div className="relative max-w-[1180px] mx-auto px-4 md:px-12 pt-10 pb-6 md:pt-14 md:pb-8 grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-6 lg:gap-10 items-center">
        <div className="min-w-0">
          <div className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[var(--banana)] flex items-center gap-3">
            <span className="w-8 h-px bg-[var(--banana)]" />
            Epoch Labs · Community launch
          </div>
          <h1 className="font-sans font-semibold text-5xl md:text-7xl tracking-[-0.045em] leading-[0.95] mt-4 text-[var(--fg-hi)]">{GOFORGE_HERO.title}</h1>
          <p className="font-serif italic text-2xl md:text-[2rem] text-[var(--banana)] mt-2">{GOFORGE_HERO.subtitle}</p>
          <p className="text-[var(--dim)] text-[15px] md:text-base max-w-[56ch] mt-4 leading-relaxed">{GOFORGE_HERO.intro}</p>

          {soon ? (
            <div className="mt-6"><ComingSoonPill /></div>
          ) : (<>
          <div data-testid="countdown-box" className="mt-6 inline-flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-[var(--border-strong)] bg-[var(--panel)] px-4 py-3">
            {round ? (
              <>
                <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-[0.16em] ${PHASE_TONE[round.phase]}`}>
                  <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-current" />
                  {PHASE_LABEL[round.phase]}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{NEXT_LABEL[round.next.label]}</span>
                <span data-testid="countdown" className="font-mono tabular-nums text-[24px] text-[var(--banana)]">{left === null ? '—' : fmtCountdown(left)}</span>
              </>
            ) : (
              <span className="font-mono text-[12px] text-[var(--dim)]">Loading the round…</span>
            )}
          </div>

          <dl aria-label="GoForge totals" className="mt-5 grid grid-cols-3 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden max-w-[560px]">
            <Stat label="Launches" value={t ? t.launches : '—'} />
            <Stat label="EPC burned" value={fmtTokens(burned)} />
            <Stat label="To creators" value={t ? fmtEth(t.fees_paid_to_creators_eth) : '—'} />
          </dl>
          </>)}
        </div>
        <GolemHero className="w-full" />
      </div>
    </section>
  );
};
