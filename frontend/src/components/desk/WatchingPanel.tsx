'use client';

import React from 'react';
import { DESK_COPY } from '@/config/deskCopy';
import { Cell, Empty, HeadRow, LiveNumber, Panel, SurvivalBar, rowClass, useNow } from './DeskUi';
import { STAGE_LABEL, ageSince, fmtCount, fmtUsdCompact, tokenLabel, type DeskPayload, type WatchingRow } from './types';

const WATCH_COLS = 'md:grid-cols-[minmax(0,1.35fr)_0.7fr_0.65fr_0.6fr_0.55fr_0.95fr_1fr]';

const STATUS: Record<WatchingRow['status'], { label: string; cls: string }> = {
  scoring: { label: 'scoring', cls: 'text-[var(--banana)]' },
  below_threshold: { label: 'below threshold', cls: 'text-[var(--dim)] text-[11.5px]' },
  unscored: { label: 'unscored', cls: 'text-[var(--faint)]' },
  awaiting_holders: { label: 'counting holders', cls: 'text-[var(--faint)]' },
  reached_tp: { label: 'reached $30K', cls: 'text-[var(--live)]' },
  excluded: { label: 'excluded', cls: 'text-[var(--faint)]' },
};

const WatchingNote: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const hidden = [
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
            {data.watching.map((r) => (
              <li key={r.token.address} className={rowClass(WATCH_COLS)}>
                <Cell label="Token" first>
                  {/* The name truncates; the DexScreener link never does */}
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="min-w-0 truncate">
                      <span className="text-[var(--fg-hi)] font-sans font-semibold text-[14px]">{tokenLabel(r.token)}</span>
                      {r.token.name && r.token.symbol && (
                        <span className="ml-2 text-[var(--dim)] text-[11.5px]">{r.token.name}</span>
                      )}
                    </span>
                    {r.token.dexscreener_url && (
                      <a
                        href={r.token.dexscreener_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`Open ${tokenLabel(r.token)} on DexScreener`}
                        className="shrink-0 inline-flex items-center px-1.5 py-px rounded border border-[var(--banana)]/40 text-[9.5px] uppercase tracking-wider text-[var(--banana)] hover:bg-[var(--banana-glow)]"
                      >
                        Dex ↗
                      </a>
                    )}
                  </span>
                </Cell>
                <Cell label="MC now">
                  <LiveNumber value={r.mc_now} className={r.status === 'reached_tp' ? 'text-[var(--live)]' : ''}>
                    {fmtUsdCompact(r.mc_now)}
                  </LiveNumber>
                </Cell>
                <Cell label="Peak"><span className="text-[var(--dim)]">{fmtUsdCompact(r.peak_mc)}</span></Cell>
                <Cell label="Age">{ageSince(r.launched_at, now)}</Cell>
                <Cell label="Holders">{fmtCount(r.holders)}</Cell>
                <Cell label="Survival">
                  <SurvivalBar value={r.survival} threshold={data.threshold} />
                </Cell>
                <Cell label="Status">
                  <span className={STATUS[r.status].cls}>{STATUS[r.status].label}</span>
                </Cell>
              </li>
            ))}
          </ol>
        </>
      )}
    </Panel>
  );
};

export const WaitingPanel: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow(10_000);
  const gated = data.state === 'gated';
  return (
    <Panel id="waiting" title="Waiting for entry" count={data.waiting.filter((w) => w.stage !== 'dropped').length} note={DESK_COPY.waitingAnon}>
      {data.waiting.length === 0 ? (
        <Empty>{gated ? 'Golem does not queue candidates until the hourglass is full.' : DESK_COPY.waitingEmpty}</Empty>
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
