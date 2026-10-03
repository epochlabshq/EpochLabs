'use client';

import React from 'react';
import { BLOCKER_LABEL, DESK_COPY } from '@/config/deskCopy';
import { useEmileStore } from '@/store/useEmileStore';
import { SimClosedRows, SimOpenRows } from './SimTables';
import { Cell, Empty, HeadRow, LiveNumber, Panel, TxLink, rowClass, useNow } from './DeskUi';
import {
  EXIT_REASON_LABEL, ageSince, fmtAmount, fmtDuration, fmtEth, fmtPrice, fmtSignedEth, fmtSignedPct, fmtUsdCompact,
  pnlTone, tokenLabel, type ClosedTrade, type DeskPayload, type OpenTrade,
} from './types';

const OPEN_COLS = 'md:grid-cols-[minmax(0,1.2fr)_0.8fr_0.9fr_0.7fr_0.9fr_1.2fr_1.3fr_0.5fr]';
const CLOSED_COLS = 'md:grid-cols-[minmax(0,1.3fr)_1.4fr_0.7fr_1.2fr_0.9fr_0.8fr_0.9fr]';

const PnlCell: React.FC<{ eth: number | null; pct: number | null; flashKey: string }> = ({ eth, pct, flashKey }) => (
  <LiveNumber value={`${flashKey}:${eth}`} className={pnlTone(eth)}>
    {fmtSignedEth(eth)} ETH <span className="text-[11px]">({fmtSignedPct(pct)})</span>
  </LiveNumber>
);

const TokenCell: React.FC<{ t: OpenTrade | ClosedTrade; onWhy: () => void }> = ({ t, onWhy }) => (
  <span className="flex items-center gap-2 min-w-0">
    <span className="text-[var(--fg-hi)] font-sans font-semibold text-[14px] truncate">{tokenLabel(t.token)}</span>
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onWhy(); }}
      className="shrink-0 px-1.5 py-0.5 rounded border border-[var(--banana)]/50 text-[10px] uppercase tracking-wider text-[var(--banana)] hover:bg-[var(--banana-glow)]"
    >
      Why
    </button>
  </span>
);

const rowInteractive = 'cursor-pointer hover:border-[var(--banana)]/50 transition-colors';

const exitRule = (t: OpenTrade) => {
  const p = t.why?.exit_plan;
  if (!p) return '—';
  return `TP ${fmtUsdCompact(p.take_profit_mc_usd)} · SL ${fmtUsdCompact(p.stop_loss_mc_usd)} · ${p.max_hold_h}h max`;
};

export const OpenPositions: React.FC<{ data: DeskPayload; onWhy: (id: string) => void }> = ({ data, onWhy }) => {
  const now = useNow(30_000);
  const flashId = useEmileStore((s) => s.deskFlashId);
  const blocked = data.state === 'gated' || data.state === 'paused';
  const simOpen = (data.simulation?.trades ?? []).filter((t) => !t.exit);
  return (
    <Panel
      id="open"
      title="Open positions"
      count={data.open.length + simOpen.length}
      note={`Live PnL values held tokens at the pair's current mid price. Click a real row for why Golem entered.${simOpen.length ? ' SIMULATION rows are paper trades: no funds, no transactions.' : ''}`}
    >
      {data.open.length === 0 && simOpen.length > 0 ? null : data.open.length === 0 ? (
        <Empty>
          {data.state === 'gated' && data.blocked_by ? (
            <>
              Golem does not open positions until the hourglass is full. Blocked by:{' '}
              <span className="font-mono text-[var(--stall)]">{data.blocked_by}</span>
              {BLOCKER_LABEL[data.blocked_by] && <> ({BLOCKER_LABEL[data.blocked_by]})</>}.
            </>
          ) : blocked ? (
            <>Golem is paused and opens no new positions. Blocked by: <span className="font-mono text-[var(--stall)]">{data.blocked_by}</span>.</>
          ) : (
            DESK_COPY.openEmpty
          )}
        </Empty>
      ) : (
        <>
          <HeadRow cols={OPEN_COLS} labels={['Token', 'Entered', 'Entry price', 'Size', 'Price now', 'PnL', 'Exit rule', 'Tx']} />
          <ol className="space-y-2 md:space-y-1.5">
            {data.open.map((t) => (
              <li
                key={t.id}
                onClick={() => onWhy(t.id)}
                className={`${rowClass(OPEN_COLS)} ${rowInteractive} ${flashId === t.id ? 'desk-row-new' : ''}`}
              >
                <Cell label="Token" first><TokenCell t={t} onWhy={() => onWhy(t.id)} /></Cell>
                <Cell label="Entered">{ageSince(t.entry.at, now)} ago</Cell>
                <Cell label="Entry price">{fmtPrice(t.entry.price)}</Cell>
                <Cell label="Size">{fmtEth(t.entry.size_eth)} ETH</Cell>
                <Cell label="Price now"><LiveNumber value={t.mark_price}>{fmtPrice(t.mark_price)}</LiveNumber></Cell>
                <Cell label="PnL"><PnlCell eth={t.pnl.eth} pct={t.pnl.pct} flashKey={t.id} /></Cell>
                <Cell label="Exit rule"><span className="text-[11.5px] text-[var(--dim)] whitespace-normal">{exitRule(t)}</span></Cell>
                <Cell label="Tx"><TxLink href={t.entry.tx_url} label="entry" /></Cell>
              </li>
            ))}
          </ol>
        </>
      )}
      <SimOpenRows trades={simOpen} data={data} />
    </Panel>
  );
};

export const ClosedTrades: React.FC<{ data: DeskPayload; onWhy: (id: string) => void }> = ({ data, onWhy }) => {
  const flashId = useEmileStore((s) => s.deskFlashId);
  const shown = data.closed.length;
  const simClosed = (data.simulation?.trades ?? []).filter((t) => t.exit);
  return (
    <Panel
      id="closed"
      title="Closed trades"
      count={data.closed_total + simClosed.length}
      note={`Every finished trade, newest first, wins and losses alike. ${data.pnl.wins} won · ${data.pnl.losses} lost${shown < data.closed_total ? ` · showing the latest ${shown}` : ''}.`}
    >
      {shown === 0 && simClosed.length > 0 ? null : shown === 0 ? (
        <Empty>{DESK_COPY.closedEmpty}</Empty>
      ) : (
        <>
          <HeadRow cols={CLOSED_COLS} labels={['Token', 'Entry → exit', 'Held', 'PnL', 'Exit reason', 'EPC burned', 'Txs']} />
          <ol className="space-y-2 md:space-y-1.5">
            {data.closed.map((t) => (
              <li
                key={t.id}
                onClick={() => onWhy(t.id)}
                className={`${rowClass(CLOSED_COLS)} ${rowInteractive} ${flashId === t.id ? 'desk-row-new' : ''}`}
              >
                <Cell label="Token" first><TokenCell t={t} onWhy={() => onWhy(t.id)} /></Cell>
                <Cell label="Entry → exit">{fmtPrice(t.entry.price)} → {fmtPrice(t.exit.price)}</Cell>
                <Cell label="Held">{fmtDuration(t.duration_s)}</Cell>
                <Cell label="PnL"><PnlCell eth={t.pnl.eth} pct={t.pnl.pct} flashKey={t.id} /></Cell>
                <Cell label="Exit reason">{t.exit_reason ? EXIT_REASON_LABEL[t.exit_reason] : <span className="text-[var(--faint)]">not logged</span>}</Cell>
                <Cell label="EPC burned">{t.epc_burned === null ? <span className="text-[var(--faint)]">—</span> : `${fmtAmount(t.epc_burned)} EPC`}</Cell>
                <Cell label="Txs">
                  <TxLink href={t.entry.tx_url} label="entry" /> · <TxLink href={t.exit.tx_url} label="exit" />
                </Cell>
              </li>
            ))}
          </ol>
        </>
      )}
      <SimClosedRows trades={simClosed} />
    </Panel>
  );
};
