'use client';

import React, { useEffect, useState } from 'react';

/** Monospace number that flashes once when its value changes (no flash under prefers-reduced-motion). */
export const LiveNumber: React.FC<{ value: string | number | null; className?: string; children?: React.ReactNode }> = ({
  value,
  className = '',
  children,
}) => {
  // Track the previous value during render (React's recommended pattern; no effect needed)
  const [prev, setPrev] = useState(value);
  const [flashes, setFlashes] = useState(0);
  if (value !== prev) {
    setPrev(value);
    setFlashes(flashes + 1);
  }
  return (
    <span key={flashes} className={`font-mono tabular-nums ${flashes ? 'desk-flash' : ''} ${className}`}>
      {children ?? value ?? '—'}
    </span>
  );
};

/** Wall clock that ticks every `ms`, for "12s ago" style labels. */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export const Panel: React.FC<{
  id: string;
  title: string;
  count?: number;
  note?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}> = ({ id, title, count, note, children, className = '' }) => (
  <section aria-labelledby={`${id}-title`} className={`min-w-0 ${className}`}>
    <div className="flex items-baseline justify-between gap-3 mb-3">
      <h2 id={`${id}-title`} className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">
        {title}
        {count !== undefined && <span className="ml-2 font-mono text-[13px] font-normal text-[var(--faint)]">{count}</span>}
      </h2>
    </div>
    {note && <p className="-mt-1 mb-3 font-mono text-[11.5px] text-[var(--dim)] leading-relaxed">{note}</p>}
    {children}
  </section>
);

/** Table header on desktop; hidden on mobile where every row is a card with inline labels. */
export const HeadRow: React.FC<{ cols: string; labels: string[] }> = ({ cols, labels }) => (
  <div
    aria-hidden
    className={`hidden md:grid ${cols} gap-x-4 px-4 pb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]`}
  >
    {labels.map((l, i) => (
      <div key={l} className={i === 0 ? '' : 'text-right'}>{l}</div>
    ))}
  </div>
);

export const Cell: React.FC<{ label: string; children: React.ReactNode; first?: boolean; className?: string; wrap?: boolean }> = ({
  label,
  children,
  first = false,
  className = '',
  wrap = false,
}) => (
  <div className={`min-w-0 ${first ? 'col-span-2 md:col-span-1' : 'md:text-right'} ${className}`}>
    {!first && <div className="md:hidden font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{label}</div>}
    <div className={`font-mono text-[12.5px] text-[var(--fg)] ${wrap ? 'whitespace-nowrap' : 'md:truncate'}`}>{children}</div>
  </div>
);

export const rowClass = (cols: string) =>
  `grid grid-cols-2 ${cols} gap-x-4 gap-y-2.5 md:gap-y-0 items-center rounded-xl md:rounded-lg border border-[var(--border)] bg-[var(--panel)] px-4 py-3`;

/** Survival as a bar with the entry threshold marked, plus the number. */
export const SurvivalBar: React.FC<{ value: number | null; threshold: number; wide?: boolean }> = ({ value, threshold, wide = false }) => (
  <span className="inline-flex items-center gap-2 justify-end">
    <span aria-hidden className={`relative hidden sm:inline-block ${wide ? 'w-24 h-2' : 'w-14 h-1.5'} rounded-full bg-[var(--soft)] overflow-hidden`}>
      {value !== null && (
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${value >= threshold ? 'bg-[var(--banana)]' : 'bg-[var(--faint)]'}`}
          style={{ width: `${Math.min(100, value * 100)}%` }}
        />
      )}
      <span className="absolute inset-y-0 w-px bg-[var(--fg-hi)]" style={{ left: `${threshold * 100}%` }} />
    </span>
    <LiveNumber value={value} className={value !== null && value >= threshold ? 'text-[var(--banana)]' : ''}>
      {value === null ? '—' : value.toFixed(2)}
    </LiveNumber>
  </span>
);

export const Empty: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">{children}</div>
);

export const TxLink: React.FC<{ href: string; label: string }> = ({ href, label }) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(e) => e.stopPropagation()}
    className="text-[var(--banana)] hover:underline"
  >
    {label}
  </a>
);
