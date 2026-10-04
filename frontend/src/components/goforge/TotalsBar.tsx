import React from 'react';
import { fmtTokens, fmtUsd, type GoForgeTotals } from './types';

const Stat: React.FC<{ label: string; value: React.ReactNode; tone?: string }> = ({ label, value, tone = 'text-[var(--fg-hi)]' }) => (
  <div className="bg-[var(--panel)] px-4 py-3 min-w-0">
    <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)] truncate">{label}</dt>
    <dd className={`mt-1 font-mono tabular-nums text-[20px] md:text-[22px] font-semibold ${tone}`}>{value}</dd>
  </div>
);

export const TotalsBar: React.FC<{ totals: GoForgeTotals }> = ({ totals }) => (
  <dl
    aria-label="GoForge totals"
    className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden"
  >
    <Stat label="Launches" value={totals.launches} />
    <Stat label="Reached $30K" value={totals.reached_30k} tone="text-[var(--live)]" />
    <Stat label="Stalled" value={totals.stalled} tone="text-[var(--stall)]" />
    <Stat label="Pending" value={totals.pending} tone="text-[var(--banana)]" />
    <Stat label="Fees routed" value={fmtUsd(totals.fees_usd)} />
    <Stat label="EPC burned" value={fmtTokens(totals.epc_burned)} />
  </dl>
);
