'use client';

import React, { useMemo, useState } from 'react';
import { NO_DATA, STATUS_HINT, STATUS_LABEL } from '@/config/radarCopy';
import { CiBar } from './CiBar';
import type { Narrative } from './types';
import { clusterColor, fmtCi, fmtLift, fmtPct, fmtTrend } from './types';

type SortKey = 'label' | 'n_total' | 'n_resolved' | 'survival_rate' | 'lift' | 'launches_7d' | 'trend_pct' | 'status';

const COLUMNS: { key: SortKey; label: string; align?: 'right' }[] = [
  { key: 'label', label: 'Narrative' },
  { key: 'n_total', label: 'Tokens', align: 'right' },
  { key: 'survival_rate', label: 'Reached $30K' },
  { key: 'lift', label: 'vs chain', align: 'right' },
  { key: 'launches_7d', label: 'Launches 7d', align: 'right' },
  { key: 'trend_pct', label: 'Trend', align: 'right' },
  { key: 'status', label: 'Status' },
];

const STATUS_ORDER: Record<string, number> = { saturated: 0, rising: 1, steady: 2, cooling: 3, low_data: 4 };

function sortValue(n: Narrative, key: SortKey): number | string | null {
  if (key === 'label') return n.label.toLowerCase();
  if (key === 'status') return STATUS_ORDER[n.status] ?? 9;
  return n[key];
}

const Status: React.FC<{ n: Narrative }> = ({ n }) => {
  const tone =
    n.status === 'saturated' ? 'border-[var(--stall)] text-[var(--stall)]'
    : n.status === 'rising' ? 'border-[var(--live)] text-[var(--live)]'
    : n.status === 'cooling' ? 'border-[var(--dim)] text-[var(--dim)]'
    : n.status === 'low_data' ? 'border-[var(--faint)] text-[var(--faint)] border-dashed'
    : 'border-[var(--border-strong)] text-[var(--fg)]';
  return (
    <span title={STATUS_HINT[n.status]} className={`inline-block rounded-full border px-2.5 py-0.5 font-mono text-[10.5px] uppercase tracking-wider ${tone}`}>
      {STATUS_LABEL[n.status] ?? n.status}
    </span>
  );
};

const RateCell: React.FC<{ n: Narrative; baseline: number | null }> = ({ n, baseline }) => {
  if (n.survival_rate == null) {
    return <span className="font-mono text-[12px] text-[var(--faint)]">{NO_DATA(n.n_resolved)}</span>;
  }
  return (
    <div className="min-w-[150px]">
      <div className="font-mono tabular-nums text-[13px] text-[var(--fg-hi)]">
        {fmtPct(n.survival_rate)} <span className="text-[var(--dim)]">[{fmtCi(n.ci)}]</span>
      </div>
      <div className="font-mono text-[10.5px] text-[var(--dim)]">
        n = {n.n_resolved}
        {n.confidence === 'low' && <span className="ml-1.5 rounded border border-dashed border-[var(--banana)] px-1 text-[var(--banana)]">low confidence</span>}
      </div>
      <div className="mt-1.5"><CiBar compact rate={n.survival_rate} ci={n.ci} baseline={baseline} confidence={n.confidence} /></div>
    </div>
  );
};

const Dot: React.FC<{ id: string }> = ({ id }) => (
  <span aria-hidden className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: clusterColor(id) }} />
);

interface Props {
  narratives: Narrative[];
  baseline: number | null;
  baselineRow?: { survival_rate: number | null; n_resolved: number } | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export const NarrativeTable: React.FC<Props> = ({ narratives, baseline, baselineRow, selectedId, onSelect }) => {
  // Default order is by resolved sample, never by rate: thin data must not sit on top
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'n_resolved', dir: 'desc' });

  const rows = useMemo(() => {
    const sorted = [...narratives].sort((a, b) => {
      const av = sortValue(a, sort.key);
      const bv = sortValue(b, sort.key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // a withheld value always sorts last, in either direction
      if (bv == null) return -1;
      const c = av < bv ? -1 : av > bv ? 1 : 0;
      return sort.dir === 'asc' ? c : -c;
    });
    return sorted;
  }, [narratives, sort]);

  const toggle = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: key === 'label' || key === 'status' ? 'asc' : 'desc' }));

  return (
    <div>
      {/* Desktop: table */}
      <div className="hidden md:block overflow-hidden rounded-2xl border border-[var(--rule)] bg-[var(--panel)]">
        <table className="w-full text-left border-collapse">
          <thead className="bg-[var(--panel2)]">
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  className={`px-3 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--faint)] ${c.align === 'right' ? 'text-right' : ''}`}
                >
                  <button type="button" onClick={() => toggle(c.key)} className="inline-flex items-center gap-1 uppercase tracking-[0.14em] hover:text-[var(--fg-hi)]">
                    {c.label}
                    <span aria-hidden className="text-[var(--banana)]">{sort.key === c.key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {baselineRow && (
              <tr className="bg-[var(--panel2)] border-t border-[var(--rule)]">
                <td className="px-3 py-2.5 text-[12.5px] font-medium text-[var(--dim)]">Chain baseline</td>
                <td className="px-3 py-2.5 text-right font-mono tabular-nums text-[12.5px] text-[var(--dim)]">{baselineRow.n_resolved.toLocaleString('en-US')} resolved</td>
                <td className="px-3 py-2.5 font-mono tabular-nums text-[12.5px] text-[var(--fg)]">{fmtPct(baselineRow.survival_rate, 1)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-[12.5px] text-[var(--dim)]">1.00x</td>
                <td colSpan={3} />
              </tr>
            )}
            {rows.map((n) => (
              <tr key={n.cluster_id} data-selected={selectedId === n.cluster_id} className="border-t border-[var(--rule)] align-top hover:bg-white/[0.04] transition-colors data-[selected=true]:bg-[var(--banana-glow)]">
                <td className="px-3 py-3 max-w-[260px]">
                  <button type="button" onClick={() => onSelect(n.cluster_id)} className="text-left inline-flex items-start gap-2 text-[var(--fg-hi)] hover:text-[var(--banana)]">
                    <span className="mt-1"><Dot id={n.cluster_id} /></span>
                    <span className="text-[13.5px] font-medium break-words">{n.label}</span>
                  </button>
                </td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-[13px]">{n.n_total}</td>
                <td className="px-3 py-3"><RateCell n={n} baseline={baseline} /></td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-[13px]">{fmtLift(n.lift)}</td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-[13px]">{n.launches_7d}</td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-[13px]">{fmtTrend(n.trend_pct)}</td>
                <td className="px-3 py-3"><Status n={n} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile: cards, no horizontal scroll */}
      <div className="md:hidden space-y-3">
        <label className="flex items-center gap-2 font-mono text-[11px] text-[var(--dim)]">
          Sort by
          <select
            value={`${sort.key}:${sort.dir}`}
            onChange={(e) => {
              const [key, dir] = e.target.value.split(':') as [SortKey, 'asc' | 'desc'];
              setSort({ key, dir });
            }}
            className="rounded border border-[var(--border-strong)] bg-[var(--panel)] px-2 py-1 text-[var(--fg)]"
          >
            {COLUMNS.map((c) => (
              <option key={c.key} value={`${c.key}:${c.key === 'label' || c.key === 'status' ? 'asc' : 'desc'}`}>{c.label}</option>
            ))}
            <option value="n_resolved:desc">Resolved sample</option>
          </select>
        </label>
        {rows.map((n) => (
          <button
            key={n.cluster_id}
            type="button"
            onClick={() => onSelect(n.cluster_id)}
            data-selected={selectedId === n.cluster_id}
            className="w-full text-left rounded-2xl border border-[var(--rule)] bg-[var(--panel)] p-4 data-[selected=true]:border-[var(--banana)]"
          >
            <div className="flex items-start justify-between gap-3">
              <span className="inline-flex items-start gap-2 text-[14px] font-medium text-[var(--fg-hi)] min-w-0">
                <span className="mt-1"><Dot id={n.cluster_id} /></span>
                <span className="break-words min-w-0">{n.label}</span>
              </span>
              <Status n={n} />
            </div>
            <div className="mt-3"><RateCell n={n} baseline={baseline} /></div>
            <dl className="mt-3 grid grid-cols-4 gap-2 font-mono text-[11px]">
              {[['Tokens', String(n.n_total)], ['vs chain', fmtLift(n.lift)], ['7d', String(n.launches_7d)], ['Trend', fmtTrend(n.trend_pct)]].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[var(--faint)] uppercase tracking-wider text-[9.5px]">{k}</dt>
                  <dd className="text-[var(--fg)] tabular-nums">{v}</dd>
                </div>
              ))}
            </dl>
          </button>
        ))}
      </div>
    </div>
  );
};
