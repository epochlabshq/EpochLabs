'use client';

import React from 'react';
import { DESK_SIMULATION } from '@/config/deskCopy';
import { fmtSignedPct, pnlTone, type PaperTrade } from './types';

const Stat: React.FC<{ label: string; children: React.ReactNode; className?: string }> = ({ label, children, className = '' }) => (
  <div>
    <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--faint)]">{label}</div>
    <div className={`font-sans font-semibold text-2xl tabular-nums text-[var(--fg-hi)] mt-1 ${className}`}>{children}</div>
  </div>
);

/** Hero card: the paper-trading book at a glance. Open positions are valued at the live price. */
export const SimulationCard: React.FC<{ trades: PaperTrade[] }> = ({ trades }) => {
  const open = trades.filter((t) => !t.exit);
  const closed = trades.filter((t) => t.exit);
  const wins = closed.filter((t) => (t.pnl_pct ?? 0) > 0).length;
  const pnls = trades.map((t) => t.pnl_pct).filter((v): v is number => v !== null);
  // Equal-weight: every simulated entry counts the same, so this is the average return per trade
  const avg = pnls.length ? pnls.reduce((a, b) => a + b, 0) / pnls.length : null;
  return (
    <div className="relative min-w-0 rounded-2xl border border-dashed border-[var(--banana)]/50 bg-[var(--panel)]/70 backdrop-blur px-6 py-7 md:px-8 md:py-9 text-center overflow-hidden">
      <div aria-hidden className="absolute inset-0 epochs-bloom opacity-40 pointer-events-none" />
      <div className="relative">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[var(--banana)]/60 font-mono text-[10.5px] uppercase tracking-[0.25em] text-[var(--banana)]">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--banana)] epochs-pulse" />
          {DESK_SIMULATION.badge}
        </div>
        <p className="font-sans font-semibold text-3xl md:text-4xl tracking-tight text-[var(--fg-hi)] mt-4">{DESK_SIMULATION.title}</p>
        <div className="grid grid-cols-4 gap-3 mt-5">
          <Stat label="Open">{open.length}</Stat>
          <Stat label="Closed">{closed.length}</Stat>
          <Stat label="Wins">{closed.length ? `${wins}/${closed.length}` : '—'}</Stat>
          <Stat label="Avg PnL" className={pnlTone(avg)}>{avg === null ? '—' : fmtSignedPct(avg)}</Stat>
        </div>
        <p className="text-[var(--dim)] text-[13px] mt-5 max-w-[44ch] mx-auto leading-relaxed">{DESK_SIMULATION.body}</p>
      </div>
    </div>
  );
};
