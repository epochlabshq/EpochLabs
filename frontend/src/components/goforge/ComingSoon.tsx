import React from 'react';
import { GOFORGE_SOON } from '@/config/goforgeCopy';

/** Shown instead of the live panels until NEXT_PUBLIC_GOFORGE_LIVE=true. Nothing here depends on the backend. */
export const ComingSoonPill: React.FC = () => (
  <span
    data-testid="coming-soon-pill"
    className="inline-flex items-center gap-2 rounded-full border border-[var(--banana)] bg-[var(--banana-glow)] px-3.5 py-1.5 font-mono text-[12px] font-semibold uppercase tracking-[0.18em] text-[var(--banana)]"
  >
    <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-current epochs-pulse" />
    {GOFORGE_SOON.badge}
  </span>
);

export const ComingSoonPanel: React.FC = () => (
  <section data-testid="coming-soon" aria-labelledby="soon-title" className="min-w-0">
    <div className="rounded-2xl border border-[var(--border-strong)] bg-[var(--panel)] p-6 md:p-8 relative overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute -right-24 -top-24 w-72 h-72 rounded-full bg-[radial-gradient(closest-side,rgba(233,159,48,.18),transparent)]" />
      <div className="relative">
        <ComingSoonPill />
        <h2 id="soon-title" className="font-sans font-semibold text-2xl md:text-3xl tracking-tight text-[var(--fg-hi)] mt-4">
          {GOFORGE_SOON.title}
        </h2>
        <p className="text-[var(--dim)] text-[15px] mt-2 max-w-[62ch] leading-relaxed">{GOFORGE_SOON.body}</p>
        <ul className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-3">
          {GOFORGE_SOON.items.map((it, i) => (
            <li key={it.title} className="rounded-xl border border-[var(--border)] bg-[var(--panel2)] px-4 py-3">
              <div className="font-mono text-[10.5px] uppercase tracking-[0.2em] text-[var(--faint)]">0{i + 1}</div>
              <div className="mt-1 text-[14.5px] font-medium text-[var(--fg-hi)]">{it.title}</div>
              <p className="mt-1 text-[13px] text-[var(--dim)] leading-snug">{it.text}</p>
            </li>
          ))}
        </ul>
      </div>
    </div>
  </section>
);
