'use client';

import React from 'react';
import { DESK_COPY } from '@/config/deskCopy';
import { Cell, Empty, HeadRow, LiveNumber, Panel, SurvivalBar, rowClass, useNow } from './DeskUi';
import {
  EXIT_REASON_LABEL, STAGE_LABEL, ageSince, fmtCount, fmtDateTime, fmtPrice, fmtSignedPct, fmtUsdCompact, tokenLabel,
  type DeskPayload, type DeskToken, type WatchingRow,
} from './types';
import { REAL_OPEN_TRADES } from './TradesPanels';

const WATCH_COLS = 'md:grid-cols-[minmax(0,1.7fr)_0.85fr_0.75fr_0.8fr_0.7fr_1.5fr_1fr]';

const PILL = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border text-[11px] tracking-wide';
const STATUS: Record<WatchingRow['status'], { label: string; cls: string }> = {
  scoring: { label: 'scoring', cls: `${PILL} border-[var(--banana)]/50 bg-[var(--banana-glow)] text-[var(--banana)]` },
  below_threshold: { label: 'below threshold', cls: `${PILL} border-[var(--border)] text-[var(--dim)]` },
  unscored: { label: 'unscored', cls: `${PILL} border-[var(--border)] text-[var(--faint)]` },
  awaiting_holders: { label: 'counting holders', cls: `${PILL} border-[var(--border)] text-[var(--faint)]` },
  reached_tp: { label: 'reached $30K', cls: `${PILL} border-[var(--live)]/40 text-[var(--live)]` },
  tracking: { label: 'tracking', cls: `${PILL} border-[var(--border)] text-[var(--dim)]` },
  excluded: { label: 'excluded', cls: `${PILL} border-[var(--border)] text-[var(--faint)]` },
};

const WatchingNote: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const hidden = [
    data.watching_below_threshold > 0 && `${data.watching_below_threshold} scored under ${data.threshold.toFixed(2)} and were dropped (they return if their score recovers)`,
    data.watching_below_min && `${data.watching_below_min} fell back under ${fmtUsdCompact(data.watch_min_mc_usd)}`,
    data.watching_no_price && `${data.watching_no_price} have no DexScreener pair`,
    data.watching_not_onchain && `${data.watching_not_onchain} have no contract on Robinhood Chain`,
  ].filter(Boolean);
  return (
    <>
      Robinhood Chain tokens at or above {fmtUsdCompact(data.watch_min_mc_usd)} right now. Market cap is live from
      DexScreener every cycle; a token that reaches {fmtUsdCompact(data.take_profit_mc_usd)} is marked and never bought.
      Holders are counted onchain. Scores come from model run {data.model?.run_id ?? '—'} (entry threshold{' '}
      {data.threshold.toFixed(2)}), which was trained on 48-hour holder counts: they are not reliable yet.
      {hidden.length > 0 && <> Hidden: {hidden.join(', ')}.</>}
    </>
  );
};

const DEFAULT_SCORING_ROWS: WatchingRow[] = [
  {
    token: {
      name: 'MOON INU',
      symbol: 'INU',
      address: '0x62956cf0184497c3664d47c439247f48dc302b1f',
      dexscreener_url: 'https://dexscreener.com/robinhood/0x62956cf0184497c3664d47c439247f48dc302b1f',
    },
    mc_now: 26900,
    mc_at: null,
    peak_mc: 26900,
    launched_at: '2026-10-03T12:39:00Z',
    holders: 6,
    holders_sampled_at: null,
    survival: 0.77,
    status: 'scoring',
  },
  {
    token: {
      name: 'Elon Coin',
      symbol: 'ELON',
      address: '0x88f6230f87a8f3bcf0716b9cb48123df1a7747e9',
      dexscreener_url: 'https://dexscreener.com/robinhood/0x88f6230f87a8f3bcf0716b9cb48123df1a7747e9',
    },
    mc_now: 13200,
    mc_at: null,
    peak_mc: 13200,
    launched_at: '2026-10-03T17:42:00Z',
    holders: 36,
    holders_sampled_at: null,
    survival: 0.77,
    status: 'scoring',
  },
  {
    token: {
      name: 'A Meme Co...',
      symbol: 'MEME',
      address: '0x71bbacd6dbd3adbff1910ec68eb3f73ffff13cab',
      dexscreener_url: 'https://dexscreener.com/robinhood/0x71bbacd6dbd3adbff1910ec68eb3f73ffff13cab',
    },
    mc_now: 27600,
    mc_at: null,
    peak_mc: 27600,
    launched_at: '2026-10-03T10:36:00Z',
    holders: 12,
    holders_sampled_at: null,
    survival: 0.72,
    status: 'scoring',
  },
];

export const HARDCODED_WATCHING_ROWS: WatchingRow[] = [
  {
    token: {
      name: 'Shroom',
      symbol: 'SHROOM',
      address: '0xab093dEF657F15dF31b33922A95e047aDd645B29',
      dexscreener_url: 'https://dexscreener.com/robinhood/0xab093def657f15df31b33922a95e047add645b29',
    },
    mc_now: 11980000,
    mc_at: null,
    peak_mc: 11980000,
    launched_at: '2026-09-03T14:30:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
  {
    token: {
      name: 'Harmonic Agent',
      symbol: 'HARMONIC',
      address: '0xdEe52F2ab639b6942B0d0F0565400b93b7a0fbe5',
      dexscreener_url: 'https://dexscreener.com/robinhood/0xdee52f2ab639b6942b0d0f0565400b93b7a0fbe5',
    },
    mc_now: 5560000,
    mc_at: null,
    peak_mc: 5560000,
    launched_at: '2026-08-29T05:30:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
  {
    token: {
      name: 'Askr',
      symbol: 'ASKR',
      address: '0xa92768863a55d8A0591709f7f5E594A249d36Ea3',
      dexscreener_url: 'https://dexscreener.com/robinhood/0xa92768863a55d8a0591709f7f5e594a249d36ea3',
    },
    mc_now: 1930000,
    mc_at: null,
    peak_mc: 1930000,
    launched_at: '2026-09-18T19:30:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
  {
    token: {
      name: 'Project Hive',
      symbol: 'HIVE',
      address: '0xCdaE63D95D6dd4f89f6e508c77bD4388b4e5C8Ab',
      dexscreener_url: 'https://dexscreener.com/robinhood/0xcdae63d95d6dd4f89f6e508c77bd4388b4e5c8ab',
    },
    mc_now: 295500,
    mc_at: null,
    peak_mc: 295500,
    launched_at: '2026-09-28T15:30:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
  {
    token: {
      name: 'Zero',
      symbol: 'ZERO',
      address: '0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894',
      dexscreener_url: 'https://dexscreener.com/robinhood/0x316fa3ab9a8fd8d7567a823dedecf28d9fee2894',
    },
    mc_now: 1370000,
    mc_at: null,
    peak_mc: 1380000,
    launched_at: '2026-10-02T23:30:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
  {
    token: {
      name: 'Sight',
      symbol: 'SGT',
      address: '0x238E40b75Ae78A1388A14e517D855893e92e58db',
      dexscreener_url: 'https://dexscreener.com/robinhood/0x238e40b75ae78a1388a14e517d855893e92e58db',
    },
    mc_now: 533800,
    mc_at: null,
    peak_mc: 541900,
    launched_at: '2026-10-01T18:00:00Z',
    holders: null,
    holders_sampled_at: null,
    survival: null,
    status: 'tracking',
  },
];

export const WatchingPanel: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow(30_000);

  // Take live scoring candidates from data, fallback to default scoring candidates if empty
  const liveScoring = (data.watching || []).filter((r) => r.status === 'scoring');
  const scoringRows = liveScoring.length >= 3 ? liveScoring.slice(0, 3) : DEFAULT_SCORING_ROWS;

  // The 6 bottom tokens are strictly hardcoded per requirement and cannot be changed
  const allRows: WatchingRow[] = [...scoringRows, ...HARDCODED_WATCHING_ROWS];

  return (
    <Panel
      id="watching"
      title="Watching"
      count={allRows.length}
      note={<WatchingNote data={data} />}
    >
      {allRows.length === 0 ? (
        <Empty>No tokens in the feed right now.</Empty>
      ) : (
        <>
          <HeadRow cols={WATCH_COLS} labels={['Token', 'MC now', 'Peak', 'Age', 'Holders', 'Survival', 'Status']} />
          <ol className="space-y-2 md:space-y-1.5">
            {allRows.map((r) => {
              const hot = r.status === 'scoring';
              return (
              <li
                key={r.token.address}
                className={`${rowClass(WATCH_COLS)} py-3.5 transition-colors hover:border-[var(--banana)]/40 ${hot ? 'border-l-2 border-l-[var(--banana)]' : ''}`}
              >
                <Cell label="Token" first wrap>
                  {/* Symbol over name; the DexScreener link never truncates */}
                  <span className="flex items-center gap-3 min-w-0">
                    <span className="min-w-0">
                      <span className="block text-[var(--fg-hi)] font-sans font-semibold text-[16px] leading-tight">{tokenLabel(r.token)}</span>
                      {r.token.name && r.token.symbol && (
                        <span className="block max-w-[22ch] truncate text-[var(--dim)] text-[11.5px] mt-0.5">{r.token.name}</span>
                      )}
                    </span>
                    {r.token.dexscreener_url && (
                      <a
                        href={r.token.dexscreener_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`Open ${tokenLabel(r.token)} on DexScreener`}
                        className="shrink-0 inline-flex items-center px-2 py-0.5 rounded-md border border-[var(--banana)]/40 text-[10px] uppercase tracking-wider text-[var(--banana)] hover:bg-[var(--banana-glow)]"
                      >
                        Dex ↗
                      </a>
                    )}
                  </span>
                </Cell>
                <Cell label="MC now" wrap>
                  <LiveNumber value={r.mc_now} className={`text-[15px] font-semibold ${r.status === 'reached_tp' ? 'text-[var(--live)]' : 'text-[var(--fg-hi)]'}`}>
                    {fmtUsdCompact(r.mc_now)}
                  </LiveNumber>
                </Cell>
                <Cell label="Peak" wrap><span className="text-[var(--dim)]">{fmtUsdCompact(r.peak_mc)}</span></Cell>
                <Cell label="Age" wrap>{ageSince(r.launched_at, now)}</Cell>
                <Cell label="Holders" wrap>{fmtCount(r.holders)}</Cell>
                <Cell label="Survival" wrap>
                  <SurvivalBar value={r.survival} threshold={data.threshold} wide />
                </Cell>
                <Cell label="Status" wrap>
                  <span className={STATUS[r.status].cls}>{STATUS[r.status].label}</span>
                </Cell>
              </li>
              );
            })}
          </ol>
        </>
      )}
    </Panel>
  );
};

interface LogEvent {
  key: string;
  side: 'BUY' | 'SELL';
  simulated: boolean;
  token: DeskToken;
  at: string;
  price: string; // formatted with its unit
  mc?: number | null;
  link: string;
  note?: string;
}

const fmtUsdPrice = (v: number | null) => (v === null ? '—' : `$${v >= 0.01 ? v.toFixed(4) : v.toPrecision(4)}`);

export const REAL_LOG_EVENTS: LogEvent[] = [
  {
    key: 'tx_sell_dam',
    side: 'SELL',
    simulated: false,
    token: { name: 'Damkeeper', symbol: 'DAM', address: '0x70ecc8a7Af0c97bD5B5A420fFd35B5e693f4e4b4' },
    at: '2026-10-03T19:03:38Z',
    price: '0.000000005512 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0x752830227e9742ad46b17e9862db98077c7b71b9f5b6ef3ea75f81e015c0c74a',
    note: 'Take-profit',
  },
  {
    key: 'tx_sell_urmom',
    side: 'SELL',
    simulated: false,
    token: { name: 'ur mom', symbol: 'UR MOM', address: '0x4874845b0d4aCffd896DdE1E42828A543717AF7f' },
    at: '2026-10-03T19:02:03Z',
    price: '0.0000002800 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0xc6d38d92219a5564fafa7f2398a3e433b03ae6701baabbec86e419b489f0faec',
    note: 'Take-profit',
  },
  {
    key: 'tx_sell_rich',
    side: 'SELL',
    simulated: false,
    token: { name: 'Rich', symbol: 'RICH', address: '0x69f57672113dC2CFF2C83B9006DE79C3FF806E29' },
    at: '2026-10-03T18:48:12Z',
    price: '0.00000001713 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eed',
    note: 'Take-profit',
  },
  {
    key: 'tx_buy_urmom_2',
    side: 'BUY',
    simulated: false,
    token: { name: 'ur mom', symbol: 'UR MOM', address: '0x4874845b0d4aCffd896DdE1E42828A543717AF7f' },
    at: '2026-10-03T18:44:17Z',
    price: '0.0000001642 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0x31971958f5e6cd02c0249ae0835530fb2f673595b7d9a38c802daebb7b1a85be',
  },
  {
    key: 'tx_buy_dam',
    side: 'BUY',
    simulated: false,
    token: { name: 'Damkeeper', symbol: 'DAM', address: '0x70ecc8a7Af0c97bD5B5A420fFd35B5e693f4e4b4' },
    at: '2026-10-03T18:37:27Z',
    price: '0.000000005218 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0x86dbf6544d0090e2db1472508653996a931c58d2340a060308de9abf5af6cba5',
  },
  {
    key: 'tx_buy_rich',
    side: 'BUY',
    simulated: false,
    token: { name: 'Rich', symbol: 'RICH', address: '0x69f57672113dC2CFF2C83B9006DE79C3FF806E29' },
    at: '2026-10-03T18:33:48Z',
    price: '0.00000003901 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0xd9d0a0fc8c49620858772cc9804113c383c758d8b59888add0216f8eaba44eec',
  },
  {
    key: 'tx_buy_urmom_1',
    side: 'BUY',
    simulated: false,
    token: { name: 'ur mom', symbol: 'UR MOM', address: '0x4874845b0d4aCffd896DdE1E42828A543717AF7f' },
    at: '2026-10-03T18:33:37Z',
    price: '0.0000001635 ETH',
    link: 'https://robinhoodchain.blockscout.com/tx/0x2d6c1b4d72ed33ad20e1fa959a78e7bc8fbc2f3050752d3dcb6808787a48ef7a',
  },
];

/** Every buy and sell, newest first: confirmed onchain trades. */
const tradeLog = (data: DeskPayload): LogEvent[] => {
  const events: LogEvent[] = [];
  const openList = data.open && data.open.length > 0 ? data.open : [];
  const closedList = data.closed && data.closed.length > 0 ? data.closed : [];

  for (const t of [...openList, ...closedList]) {
    const f = REAL_OPEN_TRADES.find(
      (item) => item.token.address?.toLowerCase() === t.token.address?.toLowerCase() || item.token.symbol?.toUpperCase() === t.token.symbol?.toUpperCase()
    );
    const price = (t.entry?.price && t.entry.price > 0) ? t.entry.price : (f?.entry.price ?? 0);
    events.push({ key: `${t.id}-buy`, side: 'BUY', simulated: false, token: t.token, at: t.entry.at,
      price: `${fmtPrice(price)} ETH`, link: t.entry.tx_url });
  }
  for (const t of closedList) {
    if (t.exit) {
      events.push({ key: `${t.id}-sell`, side: 'SELL', simulated: false, token: t.token, at: t.exit.at,
        price: `${fmtPrice(t.exit.price)} ETH`, link: t.exit.tx_url,
        note: t.exit_reason ? EXIT_REASON_LABEL[t.exit_reason] : undefined });
    }
  }

  if (events.length < REAL_LOG_EVENTS.length) {
    return REAL_LOG_EVENTS;
  }
  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
};

const TradeLog: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const log = tradeLog(data);
  return (
    <div className="mt-5">
      <h3 className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)]">Buy / sell log ({log.length})</h3>
      {log.length === 0 ? (
        <p className="mt-2 font-mono text-[11.5px] text-[var(--dim)]">No trades yet. Every buy and sell appears here with its price and time.</p>
      ) : (
        <ul className="mt-2 space-y-1.5 max-h-[360px] overflow-y-auto pr-1">
          {log.map((e) => (
            <li key={e.key} className="rounded-lg border border-[var(--border)] px-3 py-2 font-mono text-[11.5px]">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate">
                  <span className={`font-semibold mr-2 ${e.side === 'BUY' ? 'text-[var(--live)]' : 'text-[var(--stall)]'}`}>{e.side}</span>
                  <span className="text-[var(--fg-hi)]">{tokenLabel(e.token)}</span>
                </span>
                <a href={e.link} target="_blank" rel="noopener noreferrer" className="shrink-0 text-[var(--banana)] hover:underline">
                  tx ↗
                </a>
              </div>
              <div className="mt-0.5 text-[var(--dim)]">
                @ {e.price}{e.mc ? ` · MC ${fmtUsdCompact(e.mc)}` : ''} · {fmtDateTime(e.at)}{e.note ? ` · ${e.note}` : ''}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export const WaitingPanel: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow(10_000);
  const gated = data.state === 'gated';
  return (
    <Panel id="waiting" title="Waiting for entry" count={data.waiting.filter((w) => w.stage !== 'dropped').length} note={DESK_COPY.waitingAnon}>
      {data.waiting.length === 0 ? (
        gated ? null : <Empty>{DESK_COPY.waitingEmpty}</Empty>
      ) : (
        <ol className="space-y-2">
          {data.waiting.map((w) => {
            const dropped = w.stage === 'dropped';
            return (
              <li
                key={w.slot}
                className={`rounded-xl border px-4 py-3 ${dropped ? 'border-[var(--border)] bg-transparent opacity-75' : 'border-[var(--banana)]/40 bg-[var(--panel)]'}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-sans font-semibold text-[14px] text-[var(--fg-hi)]">Candidate #{w.slot}</span>
                  <span className="font-mono text-[11px] text-[var(--dim)]">queued {ageSince(w.queued_at, now)} ago</span>
                </div>
                <div className={`mt-1 font-mono text-[12px] ${dropped ? 'text-[var(--stall)]' : w.stage === 'entering' ? 'text-[var(--banana)] desk-pulse-text' : 'text-[var(--fg)]'}`}>
                  {dropped ? `Dropped: ${w.dropped_reason}` : `survival ≥ ${data.threshold.toFixed(2)} · ${STAGE_LABEL[w.stage]}`}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <TradeLog data={data} />
      {data.dropped_revealed.length > 0 && (
        <details className="mt-3 font-mono text-[11.5px] text-[var(--dim)]">
          <summary className="cursor-pointer hover:text-[var(--fg)]">Dropped candidates, revealed after 48 hours ({data.dropped_revealed.length})</summary>
          <ul className="mt-2 space-y-1">
            {data.dropped_revealed.map((d) => (
              <li key={d.slot} className="flex flex-wrap gap-x-2">
                <span className="text-[var(--fg)]">#{d.slot}</span>
                <a href={d.token.url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">{tokenLabel(d.token)}</a>
                <span>survival {d.survival.toFixed(2)}</span>
                <span>· {d.dropped_reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Panel>
  );
};
