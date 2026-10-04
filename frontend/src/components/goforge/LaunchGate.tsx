'use client';

import React from 'react';
import { GATE_COPY, GATE_LABEL } from '@/config/goforgeCopy';
import { fmtDateTime, type Gate, type GateCondition, type GateStatus } from './types';

const BADGE: Record<GateStatus, string> = {
  locked: 'border-[var(--stall)] text-[var(--stall)] bg-[var(--stall-glow)]',
  ready: 'border-[var(--live)] text-[var(--live)] bg-[var(--live-glow)]',
  forging: 'border-[var(--banana)] text-[var(--banana)] bg-[var(--banana-glow)]',
  cooldown: 'border-[var(--banana)] text-[var(--banana)] bg-transparent',
};

export const GateBadge: React.FC<{ status: GateStatus | null }> = ({ status }) =>
  status ? (
    <span
      data-testid="gate-badge"
      className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 font-mono text-[12px] font-semibold uppercase tracking-[0.18em] ${BADGE[status]}`}
    >
      <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-current" />
      {GATE_LABEL[status]}
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 font-mono text-[12px] uppercase tracking-[0.18em] text-[var(--faint)]">
      Gate unavailable
    </span>
  );

const Condition: React.FC<{ c: GateCondition; blocking: boolean }> = ({ c, blocking }) => {
  const hasProgress = typeof c.target === 'number' && c.current !== undefined;
  const pct = hasProgress && c.current !== null && c.target ? Math.min(100, ((c.current as number) / c.target) * 100) : 0;
  const isEth = c.key === 'capital';
  const fmt = (v: number | null | undefined) => (v === null || v === undefined ? '—' : isEth ? `${v.toFixed(3)} ETH` : String(v));
  return (
    <li className={`rounded-xl border px-4 py-3 ${c.passed ? 'border-[var(--border)]' : blocking ? 'border-[var(--stall)]/50' : 'border-[var(--border)]'} bg-[var(--panel)]`}>
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`mt-0.5 w-5 h-5 shrink-0 rounded-full border flex items-center justify-center text-[11px] font-bold ${
            c.passed ? 'border-[var(--live)] text-[var(--live)]' : 'border-[var(--stall)] text-[var(--stall)]'
          }`}
        >
          {c.passed ? '✓' : '✕'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-[var(--fg-hi)] leading-snug">{c.label}</div>
          <div className="sr-only">{c.passed ? 'Passed' : 'Not yet'}</div>
          {hasProgress && (
            <div className="mt-2">
              <div className="flex items-baseline justify-between font-mono text-[11.5px] text-[var(--dim)]">
                <span className="tabular-nums">{fmt(c.current)} / {fmt(c.target)}</span>
                {c.passed && <span className="text-[var(--live)]">done</span>}
              </div>
              <div aria-hidden className="mt-1 h-1.5 rounded-full bg-[var(--soft)] overflow-hidden">
                <div className={`h-full rounded-full ${c.passed ? 'bg-[var(--live)]' : 'bg-[var(--banana)]'}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}
        </div>
      </div>
    </li>
  );
};

export const LaunchGatePanel: React.FC<{ gate: Gate | null }> = ({ gate }) => (
  <section aria-labelledby="gate-title" className="min-w-0">
    <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
      <h2 id="gate-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">Launch Gate</h2>
      <GateBadge status={gate?.status ?? null} />
    </div>
    {!gate ? (
      <div role="status" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">
        The gate is built from the Desk&apos;s onchain trades. They could not be read right now, so no gate is shown instead of a guessed one.
      </div>
    ) : (
      <>
        <p className="text-[14px] text-[var(--dim)] mb-1">{GATE_COPY[gate.status]}</p>
        {gate.status === 'locked' && gate.blocked_by.length > 0 && (
          <p className="font-mono text-[11.5px] text-[var(--stall)] mb-3">
            blocked by: {gate.blocked_by.join(', ')}
          </p>
        )}
        {gate.next_launch_possible_at && (
          <p className="font-mono text-[11.5px] text-[var(--banana)] mb-3">
            next launch possible after {fmtDateTime(gate.next_launch_possible_at)}
          </p>
        )}
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          {gate.conditions.map((c) => (
            <Condition key={c.key} c={c} blocking={gate.blocked_by.includes(c.key)} />
          ))}
        </ul>
      </>
    )}
  </section>
);
