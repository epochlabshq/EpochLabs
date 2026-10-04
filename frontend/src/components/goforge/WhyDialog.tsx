'use client';

import React, { useEffect, useRef } from 'react';
import { CopyButton } from './CopyButton';
import { fmtDateTime, type Launch } from './types';

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-3 gap-y-0.5 py-2 border-b border-[var(--rule)] last:border-0">
    <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--faint)] pt-0.5">{label}</dt>
    <dd className="min-w-0 text-[13.5px] text-[var(--fg)] break-words">{children}</dd>
  </div>
);

/** "Why Golem launched it": the decision as logged before the result, with the hash that proves it was not edited. */
export const WhyDialog: React.FC<{ launch: Launch | null; onClose: () => void }> = ({ launch, onClose }) => {
  const closeRef = useRef<HTMLButtonElement>(null);
  const open = launch !== null;

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!launch) return null;
  const w = launch.why;
  const hasAny = Object.values(w).some((v) => v !== null && v !== '');

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-6">
      <div aria-hidden className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="goforge-why-title"
        className="relative w-full sm:max-w-[620px] max-h-[88vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-[var(--border-strong)] bg-[var(--panel)] shadow-2xl"
      >
        <div className="sticky top-0 flex items-start justify-between gap-3 px-5 pt-5 pb-3 bg-[var(--panel)] border-b border-[var(--rule)]">
          <div className="min-w-0">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.2em] text-[var(--banana)]">Why Golem launched it</div>
            <h3 id="goforge-why-title" className="font-sans font-semibold text-xl text-[var(--fg-hi)] truncate mt-1">
              {launch.name ?? launch.symbol ?? launch.id}
              {launch.symbol && launch.name ? ` · ${launch.symbol}` : ''}
            </h3>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 w-8 h-8 rounded-lg border border-[var(--border-strong)] text-[var(--dim)] hover:text-[var(--fg-hi)] font-mono"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          {!hasAny && (
            <p className="text-[14px] text-[var(--dim)]">
              No decision log entry has been attached to this launch yet. Nothing is shown rather than a reconstructed reason.
            </p>
          )}
          <dl>
            {w.window !== null && <Row label="Launch window">{w.window}</Row>}
            {w.window_reason !== null && <Row label="Why this window">{w.window_reason}</Row>}
            {w.lore_summary !== null && <Row label="Lore">{w.lore_summary}</Row>}
            {w.model_run_id !== null && (
              <Row label="Model run">
                <span className="font-mono">{String(w.model_run_id)}</span>
              </Row>
            )}
            {launch.launched_at && <Row label="Launched">{fmtDateTime(launch.launched_at)}</Row>}
          </dl>
          <div className="rounded-xl border border-[var(--border)] bg-[var(--panel2)] px-4 py-3">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--faint)]">Decision log hash</div>
            {w.why_hash ? (
              <div className="mt-1 flex items-start gap-2">
                <code className="font-mono text-[11.5px] text-[var(--fg)] break-all min-w-0 flex-1">{w.why_hash}</code>
                <CopyButton value={w.why_hash} label="decision log hash" />
              </div>
            ) : (
              <p className="mt-1 font-mono text-[12px] text-[var(--dim)]">not recorded</p>
            )}
            <p className="mt-2 text-[12px] text-[var(--dim)]">
              Compare it with the hash in Golem&apos;s decision log. If the reason had been rewritten after the result, they would not match.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
