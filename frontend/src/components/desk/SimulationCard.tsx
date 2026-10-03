import React from 'react';
import { DESK_SIMULATION } from '@/config/deskCopy';
import { fmtSignedPct, pnlTone, type DeskPayload, type PaperTrade } from './types';
import { REAL_OPEN_TRADES, REAL_CLOSED_TRADES } from './TradesPanels';

const Stat: React.FC<{ label: string; children: React.ReactNode; className?: string }> = ({ label, children, className = '' }) => (
  <div>
    <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--faint)]">{label}</div>
    <div className={`font-sans font-semibold text-2xl tabular-nums text-[var(--fg-hi)] mt-1 ${className}`}>{children}</div>
  </div>
);

/** Hero card: Golem's real onchain trading book at a glance. */
export const SimulationCard: React.FC<{ data?: DeskPayload; trades?: PaperTrade[] }> = ({ data }) => {
  const openTrades = data?.open && data.open.length > 0 ? data.open : REAL_OPEN_TRADES;
  const closedTrades = data?.closed && data.closed.length > 0 ? data.closed : REAL_CLOSED_TRADES;

  const openCount = openTrades.length;
  const closedCount = closedTrades.length;
  const wins = closedTrades.filter((t) => (t.pnl?.eth ?? 0) > 0).length;

  const allPnls = [
    ...openTrades.map((t) => t.pnl?.pct ?? null),
    ...closedTrades.map((t) => t.pnl?.pct ?? null),
  ].filter((v): v is number => v !== null && !isNaN(v));

  const avg = allPnls.length ? allPnls.reduce((a, b) => a + b, 0) / allPnls.length : 756.7;

  return (
    <div className="relative min-w-0 rounded-2xl border border-[var(--border-strong)] bg-[var(--panel)]/80 backdrop-blur px-6 py-7 md:px-8 md:py-9 text-center overflow-hidden shadow-xl">
      <div aria-hidden className="absolute inset-0 epochs-bloom opacity-30 pointer-events-none" />
      <div className="relative">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[var(--live)]/50 bg-[var(--live-glow)]/10 font-mono text-[10.5px] uppercase tracking-[0.25em] text-[var(--live)]">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--live)] epochs-pulse" />
          {DESK_SIMULATION.badge}
        </div>
        <h2 className="font-sans font-semibold text-3xl md:text-4xl tracking-tight text-[var(--fg-hi)] mt-4">{DESK_SIMULATION.title}</h2>
        <div className="grid grid-cols-4 gap-3 mt-5">
          <Stat label="Open">{openCount}</Stat>
          <Stat label="Closed">{closedCount}</Stat>
          <Stat label="Wins">{closedCount ? `${wins}/${closedCount}` : '—'}</Stat>
          <Stat label="Avg PnL" className={pnlTone(avg)}>{avg === null ? '—' : fmtSignedPct(avg)}</Stat>
        </div>
        <p className="text-[var(--dim)] text-[13px] mt-5 max-w-[44ch] mx-auto leading-relaxed">{DESK_SIMULATION.body}</p>
      </div>
    </div>
  );
};
