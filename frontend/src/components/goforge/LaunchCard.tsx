'use client';

import React from 'react';
import { VERDICT_LABEL } from '@/config/goforgeCopy';
import { LiveNumber, useNow } from '@/components/desk/DeskUi';
import { CopyButton } from './CopyButton';
import { HookRulesBlock } from './HookRules';
import { MiniChart } from './MiniChart';
import {
  fmtAgo, fmtCountdown, fmtDateTime, fmtInt, fmtPriceUsd, fmtTokens, fmtUsd, secondsToVerdict, shortAddr,
  VERDICT_WINDOW_H, type Launch, type Verdict,
} from './types';

// Stalled is as loud as a success: same size, same weight, its own color and glyph (never color alone)
const VERDICT_STYLE: Record<Verdict, { box: string; glyph: string }> = {
  pending: { box: 'border-[var(--banana)] text-[var(--banana)] bg-transparent', glyph: '◔' },
  reached_30k: { box: 'border-[var(--live)] text-[var(--live)] bg-[var(--live-glow)]', glyph: '✓' },
  stalled: { box: 'border-[var(--stall)] text-[var(--stall)] bg-[var(--stall-glow)]', glyph: '✕' },
};

export const VerdictBadge: React.FC<{ verdict: Verdict }> = ({ verdict }) => (
  <span
    data-testid="verdict-badge"
    data-verdict={verdict}
    className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 font-mono text-[12px] font-semibold uppercase tracking-[0.14em] ${VERDICT_STYLE[verdict].box}`}
  >
    <span aria-hidden>{VERDICT_STYLE[verdict].glyph}</span>
    {VERDICT_LABEL[verdict]}
  </span>
);

const Stat: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="min-w-0">
    <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)] truncate">{label}</dt>
    <dd className="mt-0.5 font-mono tabular-nums text-[15px] text-[var(--fg-hi)] truncate">{children}</dd>
  </div>
);

const ExtLink: React.FC<{ href: string; children: React.ReactNode }> = ({ href, children }) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(e) => e.stopPropagation()}
    className="text-[var(--banana)] hover:underline"
  >
    {children}
  </a>
);

// X turns $TICKER into a stock card: share text never carries a "$" before a word
const xShareUrl = (text: string) =>
  `https://x.com/intent/post?text=${encodeURIComponent(text.replace(/\$(?=\w)/g, ''))}`;

export const LaunchCard: React.FC<{ launch: Launch; flash: boolean; onWhy: (l: Launch) => void }> = ({ launch: l, flash, onWhy }) => {
  const now = useNow(1000);
  const left = l.verdict === 'pending' ? secondsToVerdict(l.launched_at, VERDICT_WINDOW_H, now) : null;
  const title = l.name ?? l.symbol ?? 'Unnamed token';
  const staleAge = l.market_updated_at ? (now - new Date(l.market_updated_at).getTime()) / 1000 : null;
  const open = () => onWhy(l);

  return (
    <article
      data-testid="launch-card"
      data-launch-id={l.id}
      aria-label={`${title} launch`}
      onClick={open}
      className={`rounded-2xl border bg-[var(--panel)] p-4 md:p-5 cursor-pointer transition-colors hover:border-[var(--border-strong)] min-w-0 ${
        flash ? 'desk-row-new border-[var(--banana)]' : 'border-[var(--border)]'
      }`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] break-words">
            {title}
            {l.symbol && l.name && <span className="ml-2 font-mono text-[14px] font-normal text-[var(--dim)]">{l.symbol}</span>}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11.5px] text-[var(--dim)]">
            <span className="inline-flex items-center gap-2 min-w-0">
              <span className="break-all" title={l.ca}>{shortAddr(l.ca)}</span>
              <CopyButton value={l.ca} label="contract address" />
            </span>
            <span>launched {fmtDateTime(l.launched_at)}</span>
          </div>
        </div>
        <VerdictBadge verdict={l.verdict} />
      </header>

      {l.verdict === 'pending' ? (
        <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">Verdict in</span>
          <span data-testid="countdown" className="font-mono tabular-nums text-[22px] text-[var(--banana)]">
            {left === null ? '—' : fmtCountdown(left)}
          </span>
          <span className="font-mono text-[11.5px] text-[var(--dim)]">
            reaches $30K within {VERDICT_WINDOW_H}h or it is marked stalled
          </span>
        </div>
      ) : (
        <p className="mt-3 font-mono text-[11.5px] text-[var(--dim)]">
          Verdict locked {fmtDateTime(l.verdict_at)}. Peak {fmtUsd(l.peak_mc_usd)} within {VERDICT_WINDOW_H}h against the $30K target. It cannot change.
        </p>
      )}

      <dl className="mt-4 grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-x-4 gap-y-3">
        <Stat label="Market cap"><LiveNumber value={l.mc_usd}>{fmtUsd(l.mc_usd)}</LiveNumber></Stat>
        <Stat label={`Peak MC (${VERDICT_WINDOW_H}h)`}>{fmtUsd(l.peak_mc_usd)}</Stat>
        <Stat label="Liquidity">{fmtUsd(l.liquidity_usd)}</Stat>
        <Stat label="Volume 24h">{fmtUsd(l.volume_24h_usd)}</Stat>
        <Stat label="Holders"><LiveNumber value={l.holders}>{fmtInt(l.holders)}</LiveNumber></Stat>
        <Stat label="Price">{fmtPriceUsd(l.price_usd)}</Stat>
      </dl>

      {l.stale && (
        <p role="status" data-testid="stale" className="mt-2 font-mono text-[11.5px] text-[var(--stall)]">
          stale · {staleAge === null ? 'no market read yet' : `updated ${fmtAgo(staleAge)} ago`}. Showing the last value read.
        </p>
      )}

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-4 items-start">
        <MiniChart id={l.id} launchedAt={l.launched_at} live={l.verdict === 'pending'} />
        <div className="space-y-4 min-w-0">
          <dl className="grid grid-cols-2 gap-x-4">
            <Stat label="Fees routed">{l.fee_router_address ? fmtUsd(l.fees_usd) : 'pending'}</Stat>
            <Stat label="EPC burned">{l.fee_router_address ? fmtTokens(l.epc_burned) : 'pending'}</Stat>
          </dl>
          <HookRulesBlock rules={l.rules} pending={l.rules_pending} />
        </div>
      </div>

      <footer className="mt-4 pt-3 border-t border-[var(--rule)] flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[12px]">
          <ExtLink href={l.links.blockscout_token}>Blockscout</ExtLink>
          <ExtLink href={l.links.launch_tx}>Launch tx</ExtLink>
          <ExtLink href={l.links.dexscreener}>DexScreener</ExtLink>
          <ExtLink href={xShareUrl(l.share_text)}>Share on X</ExtLink>
        </div>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); open(); }}
          className="font-mono text-[12px] text-[var(--banana)] hover:underline"
        >
          Why Golem launched it →
        </button>
      </footer>
    </article>
  );
};
