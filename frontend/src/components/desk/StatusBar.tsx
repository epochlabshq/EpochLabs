'use client';

import React, { useEffect, useRef } from 'react';
import { BLOCKER_LABEL, DESK_STATE_COPY, DESK_STATE_LABEL } from '@/config/deskCopy';
import { LiveNumber, useNow } from './DeskUi';
import { fmtAmount, fmtDuration, fmtEth, fmtSignedEth, fmtSignedPct, pnlTone, type DeskPayload, type DeskState } from './types';

// One clip exists (Golem typing at the keyboard). It plays while Golem is in a position; in every other state
// the frame holds still and the treatment changes, until dedicated pose illustrations exist.
const GOLEM_TREATMENT: Record<DeskState, string> = {
  gated: 'grayscale opacity-45',
  watching: 'opacity-85',
  waiting: 'opacity-95 ring-2 ring-[var(--banana)]/70',
  entering: 'opacity-100 ring-2 ring-[var(--banana)] desk-ring',
  in_position: 'opacity-100 ring-2 ring-[var(--live)]/70',
  paused: 'grayscale-[.6] opacity-70 ring-2 ring-[var(--stall)]/70',
};

const STATE_DOT: Record<DeskState, string> = {
  gated: 'bg-[var(--faint)]',
  watching: 'bg-[var(--banana)]',
  waiting: 'bg-[var(--banana)] epochs-pulse',
  entering: 'bg-[var(--banana)] epochs-pulse',
  in_position: 'bg-[var(--live)]',
  paused: 'bg-[var(--stall)]',
};

const GolemPose: React.FC<{ state: DeskState }> = ({ state }) => {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (state === 'in_position' && !reduce) v.play().catch(() => {});
    else v.pause();
  }, [state]);
  return (
    <div className="relative shrink-0">
      <video
        ref={ref}
        src="/videos/Stone_golem_types_at_keyboard.mp4#t=0.5"
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden
        className={`w-10 h-10 sm:w-14 sm:h-14 md:w-16 md:h-16 rounded-xl object-cover border border-[var(--border-strong)] transition ${GOLEM_TREATMENT[state]}`}
      />
      {state === 'gated' && (
        <span aria-hidden className="absolute -top-1.5 -right-1 font-mono text-[11px] text-[var(--dim)] desk-zz">z</span>
      )}
    </div>
  );
};

const Stat: React.FC<{ label: string; children: React.ReactNode; sub?: React.ReactNode }> = ({ label, children, sub }) => (
  <div className="min-w-0">
    <div className="font-mono text-[9.5px] sm:text-[10px] uppercase tracking-[0.12em] sm:tracking-[0.16em] text-[var(--faint)] truncate">{label}</div>
    <div className="mt-0.5 text-[13px] sm:text-[15px] md:text-[17px] font-semibold text-[var(--fg-hi)] truncate">{children}</div>
    {sub && <div className="hidden sm:block font-mono text-[10.5px] text-[var(--dim)] truncate">{sub}</div>}
  </div>
);

export const StatusBar: React.FC<{ data: DeskPayload }> = ({ data }) => {
  const now = useNow();
  const lastAt = data.heartbeat.last_decision_at;
  const ageS = lastAt ? Math.max(0, (now - new Date(lastAt).getTime()) / 1000) : null;
  const warnAfter = data.heartbeat.warn_after_s;
  const stale = ageS === null || ageS > warnAfter;
  const blocker = data.blocked_by;

  return (
    <div className="sticky top-0 z-30 border-b border-[var(--rule)] bg-[var(--panel-glass)] backdrop-blur-md">
      <div className="max-w-[1180px] mx-auto px-4 md:px-12 py-2 sm:py-3 grid grid-cols-1 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,2fr)] gap-2 sm:gap-3 lg:gap-8 items-center">
        <div className="flex items-center gap-3 min-w-0" aria-live="polite">
          <GolemPose state={data.state} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${STATE_DOT[data.state]}`} />
              <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-[var(--banana)] font-semibold">
                {DESK_STATE_LABEL[data.state]}
              </span>
            </div>
            <p className="hidden sm:block text-[13px] text-[var(--fg)] leading-snug mt-0.5">{DESK_STATE_COPY[data.state]}</p>
            {blocker && (data.state === 'gated' || data.state === 'paused') && (
              <p className="font-mono text-[11.5px] text-[var(--stall)] mt-0.5">
                Blocked by: {blocker}
                {BLOCKER_LABEL[blocker] && <span className="text-[var(--dim)]"> · {BLOCKER_LABEL[blocker]}</span>}
              </p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-4 gap-x-3 sm:gap-x-4 gap-y-2.5">
          <Stat label="Wallet" sub={`start ${fmtEth(data.wallet.start_eth, 1)} ETH`}>
            <LiveNumber value={data.wallet.eth}>{data.wallet.eth === null ? 'unavailable' : `${fmtEth(data.wallet.eth)} ETH`}</LiveNumber>
          </Stat>
          <Stat
            label="Net PnL"
            sub={data.pnl.complete ? `${data.pnl.wins}W · ${data.pnl.losses}L` : 'open mark unavailable'}
          >
            <LiveNumber value={data.pnl.eth} className={pnlTone(data.pnl.eth)}>
              {fmtSignedEth(data.pnl.eth)} <span className="hidden sm:inline text-[12px]">({fmtSignedPct(data.pnl.pct)})</span>
            </LiveNumber>
          </Stat>
          <Stat
            label="EPC burned"
            sub={
              data.epc_burned.burn_address?.url ? (
                <a href={data.epc_burned.burn_address.url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
                  burn address
                </a>
              ) : (
                'burn address not set'
              )
            }
          >
            <LiveNumber value={data.epc_burned.amount_wei}>{fmtAmount(data.epc_burned.amount)} EPC</LiveNumber>
          </Stat>
          <Stat
            label="Last decision"
            sub={stale ? <span className="text-[var(--stall)]">no decision in over {Math.round(warnAfter / 60)} minutes</span> : 'Golem is alive'}
          >
            <span className={`font-mono tabular-nums ${stale ? 'text-[var(--stall)]' : ''}`}>
              {ageS === null ? 'no decision yet' : `${fmtDuration(ageS)} ago`}
            </span>
          </Stat>
        </div>
      </div>
    </div>
  );
};
