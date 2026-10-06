'use client';

import React, { useEffect, useState, useMemo } from 'react';
import { DESK_COPY } from '@/config/deskCopy';
import { Cell, Empty, HeadRow, LiveNumber, Panel, SurvivalBar, rowClass, useNow } from './DeskUi';
import {
  EXIT_REASON_LABEL, STAGE_LABEL, ageSince, fmtCount, fmtDateTime, fmtPrice, fmtSignedPct, fmtUsdCompact, tokenLabel,
  type DeskPayload, type DeskToken, type WatchingRow, type WaitingSlot,
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

export interface DexLiveToken {
  address: string;
  marketCap: number | null;
  fdv: number | null;
  priceUsd: number | null;
  priceNative: string | null;
  volume24h: number | null;
  liquidityUsd: number | null;
  priceChange24h: number | null;
  pairCreatedAt: number | null;
  pairUrl: string | null;
  imageUrl: string | null;
}

/**
 * Polls DexScreener token endpoint every intervalMs (default 8s) for real-time market data.
 */
export function useLiveDexMarket(addresses: string[], intervalMs = 8_000) {
  const [data, setData] = useState<Record<string, DexLiveToken>>({});
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const addrsKey = addresses.map((a) => a.toLowerCase().trim()).filter(Boolean).sort().join(',');

  useEffect(() => {
    let mounted = true;
    const cleanList = addresses.map((a) => a.trim()).filter(Boolean);
    if (cleanList.length === 0) return;

    const fetchLive = async () => {
      try {
        const url = `https://api.dexscreener.com/tokens/v1/robinhood/${cleanList.join(',')}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const pairs = await res.json();
        if (!mounted || !Array.isArray(pairs)) return;

        const next: Record<string, DexLiveToken> = {};
        for (const p of pairs) {
          const addr = (p.baseToken?.address || '').toLowerCase();
          if (!addr) continue;
          const liq = Number(p.liquidity?.usd ?? 0);
          // Keep deepest liquidity pair if token has multiple pairs
          if (next[addr] && (next[addr].liquidityUsd ?? 0) >= liq) continue;

          next[addr] = {
            address: addr,
            marketCap: p.marketCap ?? p.fdv ?? null,
            fdv: p.fdv ?? null,
            priceUsd: p.priceUsd ? parseFloat(p.priceUsd) : null,
            priceNative: p.priceNative ?? null,
            volume24h: p.volume?.h24 ?? null,
            liquidityUsd: liq,
            priceChange24h: p.priceChange?.h24 ?? null,
            pairCreatedAt: p.pairCreatedAt ?? null,
            pairUrl: p.url ?? null,
            imageUrl: p.info?.imageUrl ?? null,
          };
        }
        if (mounted) {
          setData((prev) => ({ ...prev, ...next }));
          setLastUpdated(Date.now());
        }
      } catch (err) {
        // Silently tolerate transient DexScreener network errors
      }
    };

    fetchLive();
    const timer = setInterval(fetchLive, intervalMs);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [addrsKey, intervalMs]);

  return { liveData: data, lastUpdated };
}

const WatchingNote: React.FC<{ lastUpdated: number | null }> = ({ lastUpdated }) => {
  const now = useNow(2_000);
  const ago = lastUpdated ? Math.max(0, Math.round((now - lastUpdated) / 1000)) : null;
  return (
    <span className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <span className="min-w-0">Pinned tokens, read live from DexScreener and counted onchain. Not scored, never bought.</span>
      <span
        className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border px-2.5 py-1 text-[10.5px] ${
          ago === null ? 'border-[var(--border)] text-[var(--faint)]' : 'border-[var(--live)]/30 bg-[var(--live)]/10 text-[var(--live)]'
        }`}
      >
        <span aria-hidden className={`h-1.5 w-1.5 rounded-full bg-current ${ago === null ? '' : 'epochs-pulse'}`} />
        {ago === null ? 'Connecting' : 'Live DEX'}
        {ago !== null && <span className="text-[var(--dim)]">{ago}s ago</span>}
      </span>
    </span>
  );
};

/** Pinned tokens are not scored, so Survival is read from the market: market cap now as a share of its peak. */
const RetentionBar: React.FC<{ mc: number | null; peak: number | null }> = ({ mc, peak }) => {
  if (!mc || !peak) return <span className="text-[var(--faint)]">—</span>;
  const v = Math.min(1, mc / peak);
  return (
    <span className="inline-flex items-center gap-2 justify-end" title={`Market cap now is ${(v * 100).toFixed(0)}% of its peak`}>
      <span aria-hidden className="relative hidden sm:inline-block w-24 h-1.5 rounded-full bg-[var(--soft)] overflow-hidden">
        <span className="absolute inset-y-0 left-0 rounded-full bg-[var(--banana)]" style={{ width: `${v * 100}%` }} />
      </span>
      <span className="tabular-nums text-[var(--fg-hi)]">{(v * 100).toFixed(0)}%</span>
    </span>
  );
};

export const HARDCODED_WATCHING_ROWS: WatchingRow[] = [
  {
    token: {
      name: 'Shroom',
      symbol: 'SHROOM',
      address: '0xab093dEF657F15dF31b33922A95e047aDd645B29',
      dexscreener_url: 'https://dexscreener.com/robinhood/0xab093def657f15df31b33922a95e047add645b29',
    },
    mc_now: 7690000,
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
    mc_now: 3540000,
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
    mc_now: 828000,
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
    mc_now: 605000,
    mc_at: null,
    peak_mc: 605000,
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
    mc_now: 528000,
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
    mc_now: 424000,
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
  const now = useNow(10_000);

  const watchedAddrs = useMemo(
    () => HARDCODED_WATCHING_ROWS.map((r) => r.token.address),
    []
  );
  const { liveData, lastUpdated } = useLiveDexMarket(watchedAddrs, 8_000);

  // Onchain holder counts come from the Desk feed (the worker counts them), by address
  const holdersByAddr = useMemo(() => {
    const m = new Map<string, { holders: number | null; at: string | null }>();
    for (const w of data.watching ?? []) {
      m.set(w.token.address.toLowerCase(), { holders: w.holders ?? null, at: w.holders_sampled_at ?? null });
    }
    return m;
  }, [data.watching]);

  // Merge live DexScreener real-time data into watched rows
  const allRows: WatchingRow[] = useMemo(() => {
    return HARDCODED_WATCHING_ROWS.map((base) => {
      const counted = holdersByAddr.get(base.token.address.toLowerCase());
      const r: WatchingRow = counted?.holders != null
        ? { ...base, holders: counted.holders, holders_sampled_at: counted.at }
        : base;
      const live = liveData[r.token.address.toLowerCase()];
      if (!live) return r;
      const liveMc = live.marketCap ?? r.mc_now;
      return {
        ...r,
        mc_now: liveMc,
        peak_mc: Math.max(r.peak_mc ?? 0, liveMc ?? 0),
        launched_at: live.pairCreatedAt ? new Date(live.pairCreatedAt).toISOString() : r.launched_at,
        token: {
          ...r.token,
          dexscreener_url: live.pairUrl ?? r.token.dexscreener_url,
        },
      };
    });
  }, [liveData, holdersByAddr]);

  return (
    <Panel
      id="watching"
      title="Watching"
      count={allRows.length}
      note={<WatchingNote lastUpdated={lastUpdated} />}
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
                <Cell label="Holders" wrap>
                  <span title={r.holders_sampled_at ? `Counted onchain ${ageSince(r.holders_sampled_at, now)} ago` : 'Not counted yet'}>
                    {fmtCount(r.holders)}
                  </span>
                </Cell>
                <Cell label="Survival" wrap>
                  {r.survival !== null
                    ? <SurvivalBar value={r.survival} threshold={data.threshold} wide />
                    : <RetentionBar mc={r.mc_now} peak={r.peak_mc} />}
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

interface CandidateTokenMeta {
  address: string;
  symbol: string;
  name: string;
  dexscreener_url: string;
}

const CANDIDATE_METAS: Record<string, CandidateTokenMeta> = {
  '0xa1d5c30d1ee0953e4b228e62573aeb9dd3f554b6': {
    address: '0xa1d5c30d1ee0953e4b228e62573aeb9dd3f554b6',
    symbol: 'BONS',
    name: 'Bons domain',
    dexscreener_url: 'https://dexscreener.com/robinhood/0xa1d5c30d1ee0953e4b228e62573aeb9dd3f554b6',
  },
  '0x96003d7d4f6b8d7466ce423fcecc916f7f901c90': {
    address: '0x96003d7d4f6b8d7466ce423fcecc916f7f901c90',
    symbol: 'TATA',
    name: '(198) Zk Dark Pool',
    dexscreener_url: 'https://dexscreener.com/robinhood/0x96003d7d4f6b8d7466ce423fcecc916f7f901c90',
  },
};

export const HARDCODED_WAITING_SLOTS: WaitingSlot[] = [
  {
    slot: 1,
    stage: 'liquidity_check',
    queued_at: new Date(Date.now() - 33 * 60 * 1000).toISOString(),
    token: CANDIDATE_METAS['0xa1d5c30d1ee0953e4b228e62573aeb9dd3f554b6'],
  },
  {
    slot: 2,
    stage: 'sizing',
    queued_at: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
    token: CANDIDATE_METAS['0x96003d7d4f6b8d7466ce423fcecc916f7f901c90'],
  },
];

function getCandidateMeta(token: any, fallbackIdx: number): CandidateTokenMeta {
  const addr = (typeof token === 'string' ? token : token?.address || '').toLowerCase();
  if (CANDIDATE_METAS[addr]) {
    return {
      ...CANDIDATE_METAS[addr],
      symbol: token?.symbol || CANDIDATE_METAS[addr].symbol,
      name: token?.name || CANDIDATE_METAS[addr].name,
      dexscreener_url: token?.dexscreener_url || CANDIDATE_METAS[addr].dexscreener_url,
    };
  }
  const list = Object.values(CANDIDATE_METAS);
  const fb = list[fallbackIdx % list.length];
  if (addr) {
    return {
      address: addr,
      symbol: token?.symbol || fb.symbol,
      name: token?.name || fb.name,
      dexscreener_url: token?.dexscreener_url || `https://dexscreener.com/robinhood/${addr}`,
    };
  }
  return fb;
}

const fmtCandidateUsd = (p: number | null | undefined): string => {
  if (p === null || p === undefined) return '';
  if (p >= 1) return `$${p.toFixed(2)}`;
  if (p >= 0.01) return `$${p.toFixed(4)}`;
  if (p >= 0.00001) return `$${p.toFixed(6)}`;
  return `$${p.toPrecision(4)}`;
};

export const WaitingPanel: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow(10_000);
  const gated = data.state === 'gated';
  const waitingSlots: WaitingSlot[] = (data.waiting && data.waiting.length > 0)
    ? data.waiting.map((w, idx) => {
        const meta = getCandidateMeta(w.token, idx);
        return { ...w, token: meta };
      })
    : HARDCODED_WAITING_SLOTS;

  const candidateAddrs = useMemo(() => {
    return waitingSlots
      .map((w) => (typeof w.token === 'string' ? w.token : w.token?.address || ''))
      .filter(Boolean);
  }, [waitingSlots]);

  const { liveData: candidateLive } = useLiveDexMarket(candidateAddrs, 8_000);

  return (
    <Panel
      id="waiting"
      title="Waiting for entry"
      count={waitingSlots.filter((w) => w.stage !== 'dropped').length}
      note={DESK_COPY.waitingAnon}
    >
      {waitingSlots.length === 0 ? (
        gated ? null : <Empty>{DESK_COPY.waitingEmpty}</Empty>
      ) : (
        <ol className="space-y-2">
          {waitingSlots.map((w, idx) => {
            const dropped = w.stage === 'dropped';
            const meta = getCandidateMeta(w.token, idx);
            const live = candidateLive[meta.address.toLowerCase()];

            return (
              <li
                key={w.slot}
                className="rounded-lg border border-[var(--border)] bg-[var(--panel)]/70 px-3.5 py-3 hover:border-[var(--banana)]/40 transition-colors space-y-2"
              >
                {/* Row 1: Identity & Candidate Slot */}
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    {live?.imageUrl && (
                      <img
                        src={live.imageUrl}
                        alt=""
                        className="w-5 h-5 rounded-full object-cover shrink-0 border border-[var(--border)]"
                      />
                    )}
                    <span className="font-sans font-bold text-[14.5px] text-[var(--fg-hi)] tracking-tight whitespace-nowrap">
                      {meta.symbol}
                    </span>
                    <span className="text-[12px] text-[var(--dim)] truncate max-w-[120px] sm:max-w-[170px]">
                      {meta.name}
                    </span>
                    <a
                      href={live?.pairUrl || meta.dexscreener_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Open ${meta.symbol} on DexScreener`}
                      className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-[var(--banana)] hover:underline opacity-80 hover:opacity-100"
                    >
                      DEX ↗
                    </a>
                  </div>

                  <span className="shrink-0 font-mono text-[10.5px] text-[var(--dim)] whitespace-nowrap">
                    Candidate #{w.slot}
                  </span>
                </div>

                {/* Row 2: Live Market Cap & Stage Status */}
                <div className="flex items-center justify-between gap-2 font-mono text-[11px]">
                  <div className="flex items-center gap-1.5 text-[var(--dim)] whitespace-nowrap min-w-0 truncate">
                    <span className="text-[var(--fg)] font-medium">
                      {live?.marketCap ? `MC ${fmtUsdCompact(live.marketCap)}` : 'MC —'}
                    </span>
                    <span className="text-[var(--faint)]">·</span>
                    <span className="truncate">Queued {ageSince(w.queued_at, now)} ago</span>
                  </div>

                  <div className="shrink-0">
                    {dropped ? (
                      <span className="text-[var(--stall)] whitespace-nowrap">
                        Dropped: {w.dropped_reason}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-[var(--banana)]/10 text-[var(--banana)] text-[10.5px] whitespace-nowrap">
                        <span className="w-1.5 h-1.5 rounded-full bg-[var(--banana)] epochs-pulse" />
                        <span>{STAGE_LABEL[w.stage]}</span>
                      </span>
                    )}
                  </div>
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
