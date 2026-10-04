'use client';

import React, { useEffect, useState } from 'react';
import { getApiBaseUrl } from '@/config/constants';
import { formatSignedEth, formatTokenAmount, formatUtc, shortAddress, type TradeItem, type TradesPayload } from './types';

const POLL_MS = 300_000;

const SIDE_STYLE: Record<TradeItem['side'], string> = {
  buy: 'text-[var(--live)] border-[var(--live)]/50',
  sell: 'text-[var(--banana)] border-[var(--banana)]/50',
  swap: 'text-[var(--dim)] border-[var(--border-strong)]',
};

const Field: React.FC<{ label: string; children: React.ReactNode; className?: string }> = ({ label, children, className = '' }) => (
  <div className={`min-w-0 ${className}`}>
    <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{label}</div>
    <div className="mt-0.5 font-mono text-[12.5px] text-[var(--fg)] break-all">{children}</div>
  </div>
);

const Result: React.FC<{ t: TradeItem }> = ({ t }) => {
  if (t.result_wei === null) return <span className="text-[var(--dim)]">{t.side === 'buy' ? 'Open' : '—'}</span>;
  const neg = t.result_wei.startsWith('-');
  return (
    <span className={neg ? 'text-[var(--stall)]' : 'text-[var(--live)]'}>
      {neg ? '▼' : '▲'} {formatSignedEth(t.result_wei)} ETH{t.partial_basis ? ' *' : ''}
    </span>
  );
};

const TradeRow: React.FC<{ t: TradeItem }> = ({ t }) => (
  <li className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 md:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2.5">
        <span className={`px-2 py-0.5 rounded border font-mono text-[10.5px] uppercase tracking-wider font-semibold ${SIDE_STYLE[t.side]}`}>{t.side}</span>
        <time dateTime={t.at} className="font-mono text-[11.5px] text-[var(--dim)]">{formatUtc(t.at)}</time>
      </div>
      <a href={t.tx_url} target="_blank" rel="noopener noreferrer" className="font-mono text-[11.5px] text-[var(--banana)] hover:underline">
        tx {shortAddress(t.tx_hash)}
      </a>
    </div>
    <div className="mt-3 grid grid-cols-2 md:grid-cols-6 gap-x-4 gap-y-3">
      <Field label="Token">
        {t.token && t.token_url ? <a href={t.token_url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">{shortAddress(t.token)}</a> : '—'}
      </Field>
      <Field label="Amount">{t.token_amount_wei ? formatTokenAmount(t.token_amount_wei) : '—'}</Field>
      <Field label="ETH">{t.eth_amount_wei ? formatTokenAmount(t.eth_amount_wei, 18, 4) : '—'}</Field>
      <Field label="Survival p">{t.survival_probability !== null ? t.survival_probability.toFixed(2) : <span className="text-[var(--faint)]">not logged</span>}</Field>
      <Field label="Top signal">{t.top_signal ?? <span className="text-[var(--faint)]">not logged</span>}</Field>
      <Field label="Result"><Result t={t} /></Field>
    </div>
  </li>
);

export const TradeJournal: React.FC = () => {
  const [data, setData] = useState<TradesPayload | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/api/trades`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload: TradesPayload = await res.json();
        if (!cancelled) { setData(payload); setError(false); }
      } catch (err) {
        console.warn('Trade data unavailable:', err);
        if (!cancelled) setError(true);
      }
    };
    load();
    const t = setInterval(load, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (!data) {
    return (
      <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">
        {error ? 'Trade data unavailable.' : 'Loading trades…'}
      </div>
    );
  }

  const s = data.summary;
  return (
    <div className="space-y-6">
      <div role="status" className={`rounded-xl border px-4 py-3 font-mono text-[12.5px] ${data.can_trade ? 'border-[var(--live)]/50 text-[var(--live)]' : 'border-[var(--stall)]/60 bg-[var(--stall-glow)] text-[var(--stall)]'}`}>
        {data.can_trade
          ? 'Golem may trade: Epoch II is complete and every gate passes on the latest model run.'
          : data.paused_reason === 'epoch_ii_not_complete'
            ? 'Golem does not trade until Epoch II is complete.'
            : `Golem paused: ${data.paused_reason}`}
      </div>

      <dl className="grid grid-cols-2 md:grid-cols-4 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden">
        {[
          ['Trades', String(s.count)],
          ['Closed', String(s.closed)],
          ['Wins / losses', `${s.wins} / ${s.losses}`],
          ['Realized', `${formatSignedEth(s.realized_wei)} ETH`],
        ].map(([label, value]) => (
          <div key={label} className="bg-[var(--panel)] px-4 py-3">
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{label}</dt>
            <dd className="mt-1 font-sans font-semibold text-xl text-[var(--fg-hi)] tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {data.trades.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--border-strong)] p-6 text-[14px] text-[var(--dim)]">
          No trades yet. The first swap from the{' '}
          <a href={data.golem_wallet_url} target="_blank" rel="noopener noreferrer" className="font-mono text-[var(--banana)] hover:underline">Golem wallet</a>{' '}
          will appear here and unlock Epoch III.
        </div>
      ) : (
        <ol className="space-y-3">
          {data.trades.map((t) => <TradeRow key={t.tx_hash} t={t} />)}
        </ol>
      )}

      <p className="font-mono text-[11px] text-[var(--faint)] leading-relaxed">
        Amounts and results are read from each swap&apos;s onchain logs. Results use average cost per token across Golem&apos;s own buys;
        * marks a sell larger than the tracked position. Survival p and top signal come from the decision Golem logged before sending the trade.
      </p>
    </div>
  );
};
