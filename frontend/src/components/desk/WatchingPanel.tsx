'use client';

import React from 'react';
import { DESK_COPY } from '@/config/deskCopy';
import { Cell, Empty, HeadRow, LiveNumber, Panel, SurvivalBar, rowClass, useNow } from './DeskUi';
import {
  EXIT_REASON_LABEL, STAGE_LABEL, ageSince, fmtCount, fmtDateTime, fmtPrice, fmtSignedPct, fmtUsdCompact, tokenLabel,
  type DeskPayload, type DeskToken, type WatchingRow,
} from './types';

const WATCH_COLS = 'md:grid-cols-[minmax(0,1.7fr)_0.85fr_0.75fr_0.8fr_0.7fr_1.5fr_1fr]';

const PILL = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border text-[11px] tracking-wide';
const STATUS: Record<WatchingRow['status'], { label: string; cls: string }> = {
  scoring: { label: 'scoring', cls: `${PILL} border-[var(--banana)]/50 bg-[var(--banana-glow)] text-[var(--banana)]` },
  below_threshold: { label: 'below threshold', cls: `${PILL} border-[var(--border)] text-[var(--dim)]` },
  unscored: { label: 'unscored', cls: `${PILL} border-[var(--border)] text-[var(--faint)]` },
  awaiting_holders: { label: 'counting holders', cls: `${PILL} border-[var(--border)] text-[var(--faint)]` },
  reached_tp: { label: 'reached $30K', cls: `${PILL} border-[var(--live)]/40 text-[var(--live)]` },
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

export const WatchingPanel: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow(30_000);
  return (
    <Panel
      id="watching"
      title="Watching"
      count={data.watching.length}
      note={<WatchingNote data={data} />}
    >
      {data.watching.length === 0 ? (
        <Empty>No tokens in the feed right now.</Empty>
      ) : (
        <>
          <HeadRow cols={WATCH_COLS} labels={['Token', 'MC now', 'Peak', 'Age', 'Holders', 'Survival', 'Status']} />
          <ol className="space-y-2 md:space-y-1.5">
            {data.watching.map((r) => {
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

/** Every buy and sell, newest first: confirmed onchain trades plus SIMULATION fills (paper trading). */
const tradeLog = (data: DeskPayload): LogEvent[] => {
  const events: LogEvent[] = [];
  for (const t of [...data.open, ...data.closed]) {
    events.push({ key: `${t.id}-buy`, side: 'BUY', simulated: false, token: t.token, at: t.entry.at,
      price: `${fmtPrice(t.entry.price)} ETH`, link: t.entry.tx_url });
  }
  for (const t of data.closed) {
    events.push({ key: `${t.id}-sell`, side: 'SELL', simulated: false, token: t.token, at: t.exit.at,
      price: `${fmtPrice(t.exit.price)} ETH`, link: t.exit.tx_url,
      note: t.exit_reason ? EXIT_REASON_LABEL[t.exit_reason] : undefined });
  }
  for (const p of data.simulation?.trades ?? []) {
    const link = p.token.dexscreener_url ?? '#';
    events.push({ key: `${p.id}-buy`, side: 'BUY', simulated: true, token: p.token, at: p.entry.at,
      price: fmtUsdPrice(p.entry.price_usd), mc: p.entry.mc_usd, link, note: `survival ${p.survival.toFixed(2)}` });
    if (p.exit) {
      events.push({ key: `${p.id}-sell`, side: 'SELL', simulated: true, token: p.token, at: p.exit.at,
        price: fmtUsdPrice(p.exit.price_usd), mc: p.exit.mc_usd, link,
        note: [p.exit_reason ? EXIT_REASON_LABEL[p.exit_reason] : null, p.pnl_pct !== null ? fmtSignedPct(p.pnl_pct) : null]
          .filter(Boolean).join(' · ') });
    }
  }
  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
};

const TradeLog: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const log = tradeLog(data);
  const sim = data.simulation?.enabled;
  return (
    <div className="mt-5">
      <h3 className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)]">Buy / sell log ({log.length})</h3>
      {sim && (
        <p className="mt-1 font-mono text-[11px] text-[var(--dim)]">
          <span className="text-[var(--banana)]">SIMULATION</span> rows are paper trades at real DexScreener prices. No funds, no transactions.
        </p>
      )}
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
                  {e.simulated && (
                    <span className="ml-2 px-1.5 py-px rounded border border-[var(--banana)]/40 text-[9.5px] tracking-wider text-[var(--banana)]">SIMULATION</span>
                  )}
                </span>
                <a href={e.link} target="_blank" rel="noopener noreferrer" className="shrink-0 text-[var(--banana)] hover:underline">
                  {e.simulated ? 'dex ↗' : 'tx ↗'}
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
