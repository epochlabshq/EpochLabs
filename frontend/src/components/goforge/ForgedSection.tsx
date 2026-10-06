'use client';

import React from 'react';
import { LaunchCard } from './LaunchCard';
import type { LaunchesPayload } from './types';

export const ForgedSection: React.FC<{ data: LaunchesPayload | null }> = ({ data }) => (
  <section id="forged" aria-labelledby="forged-title" className="min-w-0 scroll-mt-24">
    <h2 id="forged-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] mb-3">
      Forged tokens
      <span className="ml-2 font-mono text-[13px] font-normal text-[var(--faint)]">{data?.launches.length ?? 0}</span>
    </h2>
    {!data ? (
      <div role="status" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">Loading the archive…</div>
    ) : data.launches.length === 0 ? (
      <div data-testid="forged-empty" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)] leading-relaxed">
        No token has been forged yet. The first winner is launched within 24 hours of its announcement, and every launch stays here for good, whether it reaches $30K or stalls.
      </div>
    ) : (
      <div className="space-y-4">
        {data.launches.map((l) => <LaunchCard key={l.id} launch={l} />)}
      </div>
    )}
  </section>
);
