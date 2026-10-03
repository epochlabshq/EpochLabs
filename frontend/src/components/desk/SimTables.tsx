'use client';

import React from 'react';
import { Cell, HeadRow, LiveNumber, rowClass, useNow } from './DeskUi';
import {
  EXIT_REASON_LABEL, ageSince, fmtDateTime, fmtDuration, fmtSignedPct, fmtUsdCompact, pnlTone, tokenLabel,
  type DeskPayload, type PaperTrade,
} from './types';

const SIM_OPEN_COLS = 'md:grid-cols-[minmax(0,1.5fr)_1fr_1fr_1fr_0.9fr_1.5fr]';
const SIM_CLOSED_COLS = 'md:grid-cols-[minmax(0,1.5fr)_1.6fr_0.7fr_0.9fr_1fr_1.4fr]';

/** Price in USD with enough digits for sub-cent tokens */
const usd = (v: number | null) => (v === null ? '—' : `$${v >= 0.01 ? v.toFixed(4) : v.toPrecision(4)}`);

export const SimBadge: React.FC = () => (
  <span className="shrink-0 px-1.5 py-px rounded border border-[var(--banana)]/40 text-[9.5px] tracking-wider text-[var(--banana)] font-mono">
    SIMULATION
  </span>
);

const SimToken: React.FC<{ t: PaperTrade }> = ({ t }) => (
  <span className="flex items-center gap-2 min-w-0">
    <span className="text-[var(--fg-hi)] font-sans font-semibold text-[14px] truncate">{tokenLabel(t.token)}</span>
    <SimBadge />
    {t.token.dexscreener_url && (
      <a
        href={t.token.dexscreener_url}
        target="_blank"
        rel="noopener noreferrer"
        className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--banana)] hover:underline"
      >
        Dex ↗
      </a>
    )}
  </span>
);

const Pnl: React.FC<{ pct: number | null; flashKey: string }> = ({ pct, flashKey }) => (
  <LiveNumber value={`${flashKey}:${pct}`} className={pnlTone(pct)}>
    {fmtSignedPct(pct, 2)}
  </LiveNumber>
);

export const SimOpenRows: React.FC<{ trades: PaperTrade[]; data: DeskPayload }> = ({ trades, data }) => {
  const now = useNow(30_000);
  if (trades.length === 0) return null;
  const rule = `TP ${fmtUsdCompact(data.take_profit_mc_usd)} · SL ${fmtUsdCompact(data.stop_loss_mc_usd ?? null)} · ${data.max_hold_h ?? 48}h max`;
  return (
    <div className="mt-3">
      <HeadRow cols={SIM_OPEN_COLS} labels={['Token', 'Entered', 'Entry price', 'Price now', 'PnL', 'Exit rule']} />
      <ol className="space-y-2 md:space-y-1.5">
        {trades.map((t) => (
          <li key={t.id} className={rowClass(SIM_OPEN_COLS)}>
            <Cell label="Token" first><SimToken t={t} /></Cell>
            <Cell label="Entered" wrap>
              {ageSince(t.entry.at, now)} ago
              <span className="block text-[10.5px] text-[var(--faint)]">{fmtDateTime(t.entry.at)}</span>
            </Cell>
            <Cell label="Entry price" wrap>{usd(t.entry.price_usd)}</Cell>
            <Cell label="Price now" wrap><LiveNumber value={t.mark_price_usd}>{usd(t.mark_price_usd)}</LiveNumber></Cell>
            <Cell label="PnL" wrap><Pnl pct={t.pnl_pct} flashKey={t.id} /></Cell>
            <Cell label="Exit rule" wrap><span className="text-[11.5px] text-[var(--dim)]">{rule}</span></Cell>
          </li>
        ))}
      </ol>
    </div>
  );
};

export const SimClosedRows: React.FC<{ trades: PaperTrade[] }> = ({ trades }) => {
  if (trades.length === 0) return null;
  return (
    <div className="mt-3">
      <HeadRow cols={SIM_CLOSED_COLS} labels={['Token', 'Entry → exit', 'Held', 'PnL', 'Exit reason', 'Exited']} />
      <ol className="space-y-2 md:space-y-1.5">
        {trades.map((t) => (
          <li key={t.id} className={rowClass(SIM_CLOSED_COLS)}>
            <Cell label="Token" first><SimToken t={t} /></Cell>
            <Cell label="Entry → exit" wrap>{usd(t.entry.price_usd)} → {usd(t.exit?.price_usd ?? null)}</Cell>
            <Cell label="Held" wrap>
              {t.exit ? fmtDuration((new Date(t.exit.at).getTime() - new Date(t.entry.at).getTime()) / 1000) : '—'}
            </Cell>
            <Cell label="PnL" wrap><Pnl pct={t.pnl_pct} flashKey={t.id} /></Cell>
            <Cell label="Exit reason" wrap>
              {t.exit_reason ? EXIT_REASON_LABEL[t.exit_reason] : <span className="text-[var(--faint)]">not logged</span>}
            </Cell>
            <Cell label="Exited" wrap>{t.exit ? fmtDateTime(t.exit.at) : '—'}</Cell>
          </li>
        ))}
      </ol>
    </div>
  );
};
