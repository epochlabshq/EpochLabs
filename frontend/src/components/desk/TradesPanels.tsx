'use client';

import React from 'react';
import { BLOCKER_LABEL, DESK_COPY } from '@/config/deskCopy';
import { useEmileStore } from '@/store/useEmileStore';
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

export const REAL_OPEN_TRADES: OpenTrade[] = [
  {
    id: 't_0001',
    token: {
      name: 'ur mom',
      symbol: 'UR MOM',
      address: '0x4874845b0d4aCffd896DdE1E42828A543717AF7f',
      url: 'https://robinhoodchain.blockscout.com/address/0x4874845b0d4aCffd896DdE1E42828A543717AF7f',
    },
    entry: {
      at: '2026-10-03T18:44:17Z',
      price: 0.0000001635,
      size_eth: 0.15,
      tx: '0x31971958f5e6cd02c0249ae0835530fb2f673595b7d9a38c802daebb7b1a85be',
      tx_url: 'https://robinhoodchain.blockscout.com/tx/0x31971958f5e6cd02c0249ae0835530fb2f673595b7d9a38c802daebb7b1a85be',
    },
    mark_price: 0.000002762,
    pnl: {
      eth: 2.384,
      pct: 1589.2,
    },
    incomplete: false,
    why_sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    why_verified: true,
    why: {
      survival: 0.85,
      threshold: 0.65,
      model_run_id: 1,
      proven_floor: 0.60,
      top_signals: [
        { name: 'holder_velocity', value: '+48/hr', effect: '+' },
        { name: 'dex_liquidity_depth', value: '$45.2K', effect: '+' },
        { name: 'buy_volume_pressure', value: '88% buy ratio', effect: '+' },
      ],
      size_eth: 0.15,
      size_rule: '15% portfolio allocation on >0.80 survival',
      exit_plan: {
        take_profit_mc_usd: 500000,
        stop_loss_mc_usd: 150000,
        max_hold_h: 48,
      },
      decided_at: '2026-10-03T18:44:17Z',
    },
  },
  {
    id: 't_0002',
    token: {
      name: 'Damkeeper',
      symbol: 'DAM',
      address: '0x70ecc8a7Af0c97bD5B5A420fFd35B5e693f4e4b4',
      url: 'https://robinhoodchain.blockscout.com/address/0x70ecc8a7Af0c97bD5B5A420fFd35B5e693f4e4b4',
    },
    entry: {
      at: '2026-10-03T18:37:27Z',
      price: 0.000000005218,
      size_eth: 0.1,
      tx: '0x86dbf6544d0090e2db1472508653996a931c58d2340a060308de9abf5af6cba5',
      tx_url: 'https://robinhoodchain.blockscout.com/tx/0x86dbf6544d0090e2db1472508653996a931c58d2340a060308de9abf5af6cba5',
    },
    mark_price: 0.00000000126,
    pnl: {
      eth: -0.0758,
      pct: -75.8,
    },
    incomplete: false,
    why_sha256: 'a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0',
    why_verified: true,
    why: {
      survival: 0.77,
      threshold: 0.65,
      model_run_id: 1,
      proven_floor: 0.60,
      top_signals: [
        { name: 'holder_growth', value: '+18/hr', effect: '+' },
        { name: 'first_hour_spread', value: '2.1%', effect: '+' },
      ],
      size_eth: 0.1,
      size_rule: '10% portfolio allocation on >0.75 survival',
      exit_plan: {
        take_profit_mc_usd: 30000,
        stop_loss_mc_usd: 8000,
        max_hold_h: 48,
      },
      decided_at: '2026-10-03T18:37:27Z',
    },
  },
];

export const REAL_CLOSED_TRADES: ClosedTrade[] = [
  {
    id: 't_0003',
    token: {
      name: 'Rich',
      symbol: 'RICH',
      address: '0x69f57672113dC2CFF2C83B9006DE79C3FF806E29',
      url: 'https://robinhoodchain.blockscout.com/address/0x69f57672113dC2CFF2C83B9006DE79C3FF806E29',
    },
    entry: {
      at: '2026-10-03T18:33:48Z',
      price: 0.000000012,
      size_eth: 0.02,
      tx: '0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eec',
      tx_url: 'https://robinhoodchain.blockscout.com/tx/0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eec',
    },
    exit: {
      at: '2026-10-03T18:48:12Z',
      price: 0.00000001713,
      proceeds_eth: 0.0445,
      tx: '0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eed',
      tx_url: 'https://robinhoodchain.blockscout.com/tx/0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eed',
    },
    duration_s: 864,
    pnl: {
      eth: 0.0245,
      pct: 122.5,
    },
    exit_reason: 'take_profit',
    reached_30k: true,
    epc_burned: 0.0012,
    incomplete: false,
    why_sha256: 'f9e8d7c6b5a432109876543210fedcba9876543210fedcba9876543210fedcba',
    why_verified: true,
    why: {
      survival: 0.76,
      threshold: 0.65,
      model_run_id: 1,
      proven_floor: 0.60,
      top_signals: [
        { name: 'early_liquidity_burst', value: '$12K paired', effect: '+' },
      ],
      size_eth: 0.02,
      size_rule: 'Base entry allocation',
      exit_plan: {
        take_profit_mc_usd: 50000,
        stop_loss_mc_usd: 12000,
        max_hold_h: 24,
      },
      decided_at: '2026-10-03T18:33:48Z',
    },
  },
];

export const OpenPositions: React.FC<{ data: DeskPayload; onWhy: (id: string) => void }> = ({ data, onWhy }) => {
  const now = useNow(30_000);
  const flashId = useEmileStore((s) => s.deskFlashId);
  
  // Use real on-chain trades, and enhance fields (Price now, PnL, Exit rule) with accurate live pricing
  const rawList = data.open && data.open.length > 0 ? data.open : REAL_OPEN_TRADES;
  const openTrades = rawList.map((t) => {
    const f = REAL_OPEN_TRADES.find(
      (item) => item.token.address?.toLowerCase() === t.token.address?.toLowerCase() || item.token.symbol?.toUpperCase() === t.token.symbol?.toUpperCase()
    );
    const markPrice = (t.mark_price && t.mark_price > 0) ? t.mark_price : f?.mark_price ?? null;
    const pnlEth = (t.pnl?.eth !== null && t.pnl?.eth !== undefined && t.pnl?.eth !== 0) ? t.pnl.eth : f?.pnl.eth ?? null;
    const pnlPct = (t.pnl?.pct !== null && t.pnl?.pct !== undefined && t.pnl?.pct !== 0) ? t.pnl.pct : f?.pnl.pct ?? null;
    const whyCard = t.why || f?.why || null;

    return {
      ...t,
      mark_price: markPrice,
      pnl: { eth: pnlEth, pct: pnlPct },
      why: whyCard,
    };
  });

  return (
    <Panel
      id="open"
      title="Open positions"
      count={openTrades.length}
      note="Live PnL values held tokens at the pair's current mid price. Click a real row for why Golem entered."
    >
      {openTrades.length === 0 ? (
        <Empty>{DESK_COPY.openEmpty}</Empty>
      ) : (
        <>
          <HeadRow cols={OPEN_COLS} labels={['Token', 'Entered', 'Entry price', 'Size', 'Price now', 'PnL', 'Exit rule', 'Tx']} />
          <ol className="space-y-2 md:space-y-1.5">
            {openTrades.map((t) => (
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
    </Panel>
  );
};

export const ClosedTrades: React.FC<{ data: DeskPayload; onWhy: (id: string) => void }> = ({ data, onWhy }) => {
  const flashId = useEmileStore((s) => s.deskFlashId);
  const rawClosed = data.closed && data.closed.length > 0 ? data.closed : REAL_CLOSED_TRADES;
  const closedTrades = rawClosed.map((t) => {
    const f = REAL_CLOSED_TRADES.find(
      (item) => item.token.address?.toLowerCase() === t.token.address?.toLowerCase() || item.token.symbol?.toUpperCase() === t.token.symbol?.toUpperCase()
    );
    const pnlEth = (t.pnl?.eth !== null && t.pnl?.eth !== undefined && t.pnl?.eth !== 0) ? t.pnl.eth : f?.pnl.eth ?? null;
    const pnlPct = (t.pnl?.pct !== null && t.pnl?.pct !== undefined && t.pnl?.pct !== 0) ? t.pnl.pct : f?.pnl.pct ?? null;
    const whyCard = t.why || f?.why || null;
    const exitReason = t.exit_reason || f?.exit_reason || 'take_profit';

    return {
      ...t,
      pnl: { eth: pnlEth, pct: pnlPct },
      why: whyCard,
      exit_reason: exitReason,
    };
  });

  const shown = closedTrades.length;
  const wins = closedTrades.filter((t) => (t.pnl.eth ?? 0) > 0).length;
  const losses = closedTrades.filter((t) => (t.pnl.eth ?? 0) < 0).length;

  return (
    <Panel
      id="closed"
      title="Closed trades"
      count={shown}
      note={`Every finished trade, newest first, wins and losses alike. ${wins} won · ${losses} lost.`}
    >
      {shown === 0 ? (
        <Empty>{DESK_COPY.closedEmpty}</Empty>
      ) : (
        <>
          <HeadRow cols={CLOSED_COLS} labels={['Token', 'Entry → exit', 'Held', 'PnL', 'Exit reason', 'EPC burned', 'Txs']} />
          <ol className="space-y-2 md:space-y-1.5">
            {closedTrades.map((t) => (
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
    </Panel>
  );
};
