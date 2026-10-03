'use client';

import React, { useEffect, useRef, useState } from 'react';
import { fetchDeskTrade } from '@/hooks/useDesk';
import {
  EXIT_REASON_LABEL, fmtDuration, fmtEth, fmtSignedEth, fmtSignedPct, fmtUsdCompact, pnlTone, tokenLabel,
  type TradeDetail,
} from './types';
import { REAL_OPEN_TRADES, REAL_CLOSED_TRADES } from './TradesPanels';

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-3 py-2 border-b border-[var(--rule)] last:border-0">
    <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--faint)] pt-0.5">{label}</dt>
    <dd className="min-w-0 text-[13.5px] text-[var(--fg)]">{children}</dd>
  </div>
);

const Body: React.FC<{ t: TradeDetail }> = ({ t }) => {
  const w = t.why;
  if (!w) {
    return (
      <p className="text-[14px] text-[var(--dim)]">
        No Why card was logged for this trade. It was not opened through Golem&apos;s decision log, so there is nothing to show
        beyond the onchain amounts.
      </p>
    );
  }
  const above = w.survival - w.threshold;
  return (
    <dl>
      <Row label="Survival">
        <span className="font-mono tabular-nums text-[var(--banana)] text-[15px] font-semibold">{w.survival.toFixed(2)}</span>
        <span className="text-[var(--dim)]"> vs entry threshold {w.threshold.toFixed(2)} ({fmtSignedEth(above, 2)})</span>
      </Row>
      <Row label="Top signals">
        <ol className="space-y-1">
          {w.top_signals.map((s) => (
            <li key={s.name} className="flex flex-wrap items-baseline gap-x-2">
              <span className={`font-mono font-bold w-3 ${s.effect === '+' ? 'text-[var(--live)]' : s.effect === '-' ? 'text-[var(--stall)]' : 'text-[var(--dim)]'}`}>
                {s.effect === '-' ? '−' : s.effect}
              </span>
              <span className="text-[var(--fg-hi)]">{s.name.replace(/_/g, ' ')}</span>
              <span className="font-mono text-[12px] text-[var(--dim)] break-all">{s.value}</span>
            </li>
          ))}
        </ol>
      </Row>
      <Row label="Model">
        run <span className="font-mono">#{w.model_run_id}</span> · proven floor <span className="font-mono">{w.proven_floor.toFixed(3)}</span>
      </Row>
      <Row label="Position size">
        <span className="font-mono">{fmtEth(w.size_eth)} ETH</span>
        <span className="text-[var(--dim)]"> · {w.size_rule}</span>
      </Row>
      <Row label="Exit plan">
        take-profit at {fmtUsdCompact(w.exit_plan.take_profit_mc_usd)} MC · stop-loss at {fmtUsdCompact(w.exit_plan.stop_loss_mc_usd)} MC ·
        time limit {w.exit_plan.max_hold_h}h
      </Row>
    </dl>
  );
};

export const WhyCardDialog: React.FC<{ tradeId: string | null; onClose: () => void }> = ({ tradeId, onClose }) => {
  const [detail, setDetail] = useState<{ id: string; trade: TradeDetail | null; error: boolean } | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const loading = tradeId !== null && detail?.id !== tradeId;

  useEffect(() => {
    if (!tradeId) return;
    let cancelled = false;

    // First check local real trades
    const localOpen = REAL_OPEN_TRADES.find((t) => t.id === tradeId);
    if (localOpen) {
      setDetail({ id: tradeId, trade: { status: 'open', ...localOpen }, error: false });
      return;
    }
    const localClosed = REAL_CLOSED_TRADES.find((t) => t.id === tradeId);
    if (localClosed) {
      setDetail({ id: tradeId, trade: { status: 'closed', ...localClosed }, error: false });
      return;
    }

    fetchDeskTrade(tradeId)
      .then((trade: TradeDetail) => { if (!cancelled) setDetail({ id: tradeId, trade, error: false }); })
      .catch(() => { if (!cancelled) setDetail({ id: tradeId, trade: null, error: true }); });
    return () => { cancelled = true; };
  }, [tradeId]);

  useEffect(() => {
    if (!tradeId) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [tradeId, onClose]);

  if (!tradeId) return null;
  const t = !loading ? detail?.trade ?? null : null;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-6">
      <div aria-hidden className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="why-title"
        className="relative w-full sm:max-w-[620px] max-h-[88vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-[var(--border-strong)] bg-[var(--panel)] shadow-2xl"
      >
        <div className="sticky top-0 flex items-start justify-between gap-3 px-5 pt-5 pb-3 bg-[var(--panel)] border-b border-[var(--rule)]">
          <div className="min-w-0">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.2em] text-[var(--banana)]">Why Golem entered</div>
            <h3 id="why-title" className="font-sans font-semibold text-xl text-[var(--fg-hi)] truncate mt-1">
              {t ? `${tokenLabel(t.token)} · ${t.id}` : tradeId}
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
          {loading && <p role="status" className="font-mono text-[13px] text-[var(--dim)]">Loading the decision log…</p>}
          {!loading && detail?.error && (
            <p role="status" className="font-mono text-[13px] text-[var(--stall)]">This trade could not be read from /api/desk.</p>
          )}
          {t && (
            <>
              <Body t={t} />

              {t.status === 'closed' && (
                <div className="rounded-xl border border-[var(--border)] bg-[var(--panel2)] px-4 py-3">
                  <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--faint)]">Result</div>
                  <div className={`mt-1 font-mono text-[16px] font-semibold ${pnlTone(t.pnl.eth)}`}>
                    {fmtSignedEth(t.pnl.eth)} ETH ({fmtSignedPct(t.pnl.pct)})
                  </div>
                  <div className="mt-1 text-[13px] text-[var(--dim)]">
                    {t.exit_reason ? EXIT_REASON_LABEL[t.exit_reason] : 'Exit reason not logged'} after {fmtDuration(t.duration_s)} ·{' '}
                    {t.reached_30k === null
                      ? 'token not labeled yet'
                      : t.reached_30k
                        ? 'the token went on to reach $30K'
                        : 'the token never reached $30K'}
                  </div>
                </div>
              )}

              <div className="font-mono text-[11px] text-[var(--dim)] space-y-1 break-all">
                <div>
                  why sha256 {t.why_sha256 ?? '—'}{' '}
                  {t.why_sha256 && (
                    <span className={t.why_verified ? 'text-[var(--live)]' : 'text-[var(--stall)]'}>
                      {t.why_verified ? '✓ matches the logged hash' : '✗ does not match the logged hash'}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-3">
                  <a href={t.entry.tx_url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">entry tx</a>
                  {t.status === 'closed' && (
                    <a href={t.exit.tx_url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">exit tx</a>
                  )}
                  {t.token.url && (
                    <a href={t.token.url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">token</a>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
