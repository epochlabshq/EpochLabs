'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { EPOCH_COPY } from '@/config/epochsCopy';
import { EpochIcon } from './EpochIcon';
import { formatTokenAmount, formatUtc, roman, shortAddress, type EpochItem, type EpochsPayload } from './types';

const STATE_STYLE: Record<EpochItem['status'], { card: string; text: string; badge: string; label: string }> = {
  locked: {
    card: 'border border-[var(--border)] bg-[var(--panel)]',
    text: 'opacity-60',
    badge: 'text-[var(--faint)] border-[var(--border)]',
    label: 'Locked',
  },
  active: {
    card: 'border-2 border-[var(--banana)] bg-[var(--panel)] epoch-card-active',
    text: '',
    badge: 'text-[var(--ink)] bg-[var(--banana)] border-[var(--banana)]',
    label: 'Now',
  },
  complete: {
    card: 'border border-[var(--banana)] bg-[var(--surface-raised)]',
    text: '',
    badge: 'text-[var(--banana)] border-[var(--banana)]/60',
    label: 'Unlocked',
  },
};

const GATE_LABELS: Record<string, string> = {
  n_samples: 'Samples',
  n_positive: 'Positives',
  auc_std: 'AUC stability',
  time_split: 'Time split',
};

const ExternalLink: React.FC<{ href: string; children: React.ReactNode }> = ({ href, children }) => (
  <a href={href} target="_blank" rel="noopener noreferrer"
    className="text-[var(--banana)] underline decoration-[var(--banana)]/40 underline-offset-2 hover:decoration-[var(--banana)] break-all">
    {children}
  </a>
);

const Bar: React.FC<{ pct: number; label: string }> = ({ pct, label }) => {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)}
      className="h-2 w-full rounded-full bg-[var(--panel2)] border border-[var(--soft)] overflow-hidden">
      <div className="h-full rounded-full bg-[var(--banana)] transition-[width] duration-700" style={{ width: `${p}%` }} />
    </div>
  );
};

const Progress: React.FC<{ epoch: EpochItem }> = ({ epoch }) => {
  const pr = epoch.progress;
  if (!pr) return null;
  if (pr.unit === 'samples') {
    return (
      <div className="mt-4 space-y-2">
        <div className="flex justify-between font-mono text-[11.5px] text-[var(--dim)]">
          <span>Labeled tokens</span>
          <span className="text-[var(--fg-hi)] tabular-nums">{pr.current.toLocaleString('en-US')} / {pr.target.toLocaleString('en-US')}</span>
        </div>
        <Bar pct={(pr.current / pr.target) * 100} label="Labeled tokens toward target" />
        <div className="font-mono text-[10.5px] text-[var(--faint)]">Model run #{pr.run_id}</div>
      </div>
    );
  }
  const sand = 100;
  return (
    <div className="mt-4 space-y-2">
      <div className="flex justify-between font-mono text-[11.5px] text-[var(--dim)]">
        <span>Sand level</span>
        <span className="text-[var(--fg-hi)] tabular-nums">{sand.toFixed(1)}%</span>
      </div>
      <Bar pct={sand} label="Sand level" />
      <div className="flex justify-between font-mono text-[11.5px] text-[var(--dim)]">
        <span>Proven floor</span>
        <span className="tabular-nums"><span className="text-[var(--fg-hi)]">{pr.current.toFixed(3)}</span> / {pr.target.toFixed(2)}</span>
      </div>
      {pr.gates && (
        <ul className="flex flex-wrap gap-1.5 pt-1" aria-label="Gates">
          {Object.entries(pr.gates).map(([gate, ok]) => (
            <li key={gate} className={`font-mono text-[10.5px] px-2 py-0.5 rounded border ${ok ? 'border-[var(--live)]/50 text-[var(--live)]' : 'border-[var(--border-strong)] text-[var(--dim)]'}`}>
              {ok ? '✓' : '✗'} {GATE_LABELS[gate] ?? gate}
            </li>
          ))}
        </ul>
      )}
      <div className="font-mono text-[10.5px] text-[var(--faint)]">Model run #{pr.run_id}</div>
    </div>
  );
};

const Proof: React.FC<{ epoch: EpochItem }> = ({ epoch }) => {
  const proof = epoch.proof;
  if (!proof || !epoch.completed_at) return null;
  return (
    <div className="mt-4 pt-3 border-t border-[var(--border)] space-y-1.5 font-mono text-[11.5px] text-[var(--dim)]">
      <div className="flex flex-wrap gap-x-2">
        <span className="text-[var(--banana)] font-semibold">Unlocked</span>
        <span>·</span>
        {proof.type === 'tx' && proof.url && proof.hash && (
          <ExternalLink href={proof.url}>tx {shortAddress(proof.hash)} on Blockscout</ExternalLink>
        )}
        {proof.type === 'model_run' && <span className="text-[var(--fg-hi)]">Model run #{proof.run_id}</span>}
        {proof.type === 'release' && proof.url && <ExternalLink href={proof.url}>GitHub release</ExternalLink>}
      </div>
      <div>
        <time dateTime={epoch.completed_at}>{formatUtc(epoch.completed_at)}</time>
      </div>
      {proof.token_address && proof.token_url && (
        <div>Token: <ExternalLink href={proof.token_url}>{proof.token_address}</ExternalLink></div>
      )}
      {proof.methodology && (
        <details className="mt-1">
          <summary className="cursor-pointer hover:text-[var(--fg)]">Methodology snapshot</summary>
          <pre className="mt-2 p-3 rounded-lg bg-[var(--ink)] border border-[var(--border)] text-[10.5px] overflow-x-auto max-h-64">
            {JSON.stringify(proof.methodology, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
};

interface Props {
  epoch: EpochItem;
  data: EpochsPayload;
  animatePour: boolean;
}

export const EpochCard: React.FC<Props> = ({ epoch, data, animatePour }) => {
  const [copied, setCopied] = useState(false);
  const copy = EPOCH_COPY[epoch.key];
  const style = STATE_STYLE[epoch.status];

  const share = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/epochs/${epoch.anchor}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard blocked: the anchor link next to it still works
    }
  };

  return (
    <article
      id={epoch.anchor}
      aria-labelledby={`${epoch.anchor}-title`}
      className={`relative overflow-hidden scroll-mt-28 rounded-xl p-5 md:p-6 transition-colors duration-500 ${style.card}`}
    >
      {epoch.status === 'active' && (
        <div aria-hidden className="pointer-events-none absolute right-6 top-0 h-full w-16">
          {[0, 0.5, 1.1, 1.7].map((d, i) => (
            <span key={d} className="epoch-grain" style={{ left: `${20 + i * 12}%`, animationDelay: `${d}s` }} />
          ))}
        </div>
      )}
      {animatePour && <div aria-hidden className="epoch-pour" />}

      <div className="relative flex items-start gap-4 md:gap-6">
        <span className={`font-serif italic text-5xl md:text-6xl leading-none w-14 md:w-20 shrink-0 ${epoch.status === 'locked' ? 'text-[var(--faint)]' : 'text-[var(--banana)]'}`}>
          {roman(epoch.id)}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id={`${epoch.anchor}-title`} className={`font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] ${style.text}`}>
              {epoch.name}
            </h2>
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border font-mono text-[10.5px] uppercase tracking-wider font-semibold ${style.badge}`}>
              <EpochIcon status={epoch.status} className="w-3.5 h-3.5" />
              {style.label}
            </span>
          </div>

          <p className={`mt-2 text-[14px] leading-relaxed text-[var(--dim)] ${style.text}`}>{copy?.oneLine}</p>

          <div className={`mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 ${style.text}`}>
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--faint)]">Trigger</span>
            <code className="font-mono text-[12.5px] text-[var(--fg)]">{copy?.trigger}</code>
          </div>

          {epoch.key === 'first_trade' && data.golem_paused && (
            <div role="status" className="mt-4 px-3 py-2 rounded-lg border border-[var(--stall)]/60 bg-[var(--stall-glow)] font-mono text-[12px] text-[var(--stall)]">
              Golem paused: {data.golem_paused}
            </div>
          )}

          {epoch.status === 'active' && <Progress epoch={epoch} />}
          {epoch.status === 'complete' && <Proof epoch={epoch} />}

          {epoch.key === 'first_trade' && epoch.status === 'complete' && (
            <Link href="/epochs/journal"
              className="mt-4 inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border border-[var(--banana)] text-[var(--banana)] font-mono text-[12px] hover:bg-[var(--banana)] hover:text-[var(--ink)] transition-colors">
              Open Trade Journal →
            </Link>
          )}

          {epoch.key === 'first_burn' && data.burns.count > 0 && (
            <div className="mt-3 font-mono text-[11.5px] text-[var(--dim)]">
              Burned so far: <span className="text-[var(--fg-hi)]">{formatTokenAmount(data.burns.total_wei)} $EPC</span> across {data.burns.count} burn{data.burns.count === 1 ? '' : 's'}
            </div>
          )}

          <div className="mt-4 flex items-center gap-3 font-mono text-[10.5px] text-[var(--faint)]">
            <a href={`#${epoch.anchor}`} className="hover:text-[var(--fg)]">#{epoch.anchor}</a>
            <button type="button" onClick={share} className="hover:text-[var(--fg)] cursor-pointer">
              {copied ? 'Link copied' : 'Copy share link'}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
};
