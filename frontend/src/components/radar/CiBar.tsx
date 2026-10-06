import React from 'react';
import type { Confidence } from './types';
import { fmtCi, fmtPct } from './types';

interface Props {
  rate: number | null;
  ci: [number, number] | null;
  baseline: number | null;
  confidence: Confidence;
  /** compact: table row. Full size: detail drawer. */
  compact?: boolean;
}

/** 0-100% track: the 95% interval as a band, the rate as a tick, the chain baseline as a dashed line. */
export const CiBar: React.FC<Props> = ({ rate, ci, baseline, confidence, compact = false }) => {
  if (rate == null || !ci || confidence === 'insufficient') return null;
  const lo = ci[0] * 100;
  const hi = ci[1] * 100;
  const label = `Survival rate ${fmtPct(rate)}, 95% interval ${fmtCi(ci)}${baseline != null ? `, chain baseline ${fmtPct(baseline)}` : ''}`;
  return (
    <div role="img" aria-label={label} className={`relative w-full rounded-full bg-[var(--soft)] ${compact ? 'h-1.5' : 'h-2.5'}`}>
      <div
        className={`absolute inset-y-0 rounded-full ${confidence === 'low' ? 'bg-[var(--banana)]/35 border border-dashed border-[var(--banana)]' : 'bg-[var(--banana)]/55'}`}
        style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 1)}%` }}
      />
      <div className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-[3px] rounded-sm bg-[var(--fg-hi)] ${compact ? 'h-3' : 'h-5'}`} style={{ left: `${rate * 100}%` }} />
      {baseline != null && (
        <div
          className={`absolute top-1/2 -translate-y-1/2 border-l border-dashed border-[var(--dim)] ${compact ? 'h-3' : 'h-6'}`}
          style={{ left: `${baseline * 100}%` }}
          title={`Chain baseline ${fmtPct(baseline)}`}
        />
      )}
    </div>
  );
};
