'use client';

import React from 'react';
import { xProfileUrl } from '@/lib/gf';
import type { Busy, Errors } from '@/hooks/useGfActions';
import { LiveNumber } from '@/components/desk/DeskUi';
import type { Me, PublicIdea, RoundPayload } from './types';

interface Props {
  round: RoundPayload | null;
  me: Me | null;
  busy: Busy;
  errors: Errors;
  votingFor: string | null;
  onVote: (ideaId: string) => void;
  onLogin: () => void;
}

const IdeaCard: React.FC<{
  idea: PublicIdea; round: RoundPayload; me: Me | null; busy: Busy; votingFor: string | null; isWinner: boolean;
  onVote: (id: string) => void; onLogin: () => void;
}> = ({ idea, round, me, busy, votingFor, isWinner, onVote, onLogin }) => {
  const mine = me?.my_vote?.round_date === round.round_date && me.my_vote.idea_id === idea.idea_id;
  const open = round.phase === 'vote' || !!round.dev_open;
  const profile = xProfileUrl(idea.creator_handle);
  return (
    <article
      data-testid="idea-card"
      data-idea-id={idea.idea_id}
      className={`rounded-2xl border bg-[var(--panel)] p-4 flex flex-col min-w-0 ${isWinner ? 'border-[var(--live)]' : mine ? 'border-[var(--banana)]' : 'border-[var(--border)]'}`}
    >
      <div className="flex gap-3 min-w-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={idea.image_url} alt={`${idea.name} artwork`} width={72} height={72} loading="lazy"
          className="w-[72px] h-[72px] rounded-xl object-cover border border-[var(--border-strong)] bg-[var(--panel2)] shrink-0" />
        <div className="min-w-0">
          <h3 className="font-sans font-semibold text-[17px] leading-tight text-[var(--fg-hi)] break-words">{idea.name}</h3>
          <div className="mt-0.5 font-mono text-[12px] text-[var(--banana)]">{idea.ticker}</div>
          {profile && (
            <a href={profile} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-block font-mono text-[11.5px] text-[var(--dim)] hover:text-[var(--banana)] hover:underline truncate max-w-full">
              by @{idea.creator_handle}
            </a>
          )}
        </div>
        {isWinner && (
          <span className="ml-auto self-start shrink-0 rounded-md border border-[var(--live)] bg-[var(--live-glow)] px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--live)]">
            ✓ Winner
          </span>
        )}
      </div>
      <p className="mt-3 text-[13px] leading-relaxed text-[var(--fg)] line-clamp-4 break-words">{idea.lore}</p>
      <div className="mt-auto pt-4 flex items-center justify-between gap-3">
        <div className="font-mono text-[12px] text-[var(--dim)]">
          <LiveNumber value={idea.votes} className="text-[16px] text-[var(--fg-hi)]">{idea.votes ?? 0}</LiveNumber> votes
        </div>
        {open ? (
          me?.wallet ? (
            <button
              type="button"
              disabled={busy !== null || mine || me.is_team}
              onClick={() => onVote(idea.idea_id)}
              className={`rounded-lg border px-3.5 py-1.5 font-mono text-[11.5px] font-semibold uppercase tracking-[0.12em] transition-colors disabled:opacity-60 ${
                mine ? 'border-[var(--banana)] text-[var(--banana)] bg-[var(--banana-glow)]' : 'border-[var(--border-strong)] text-[var(--fg-hi)] hover:border-[var(--banana)] hover:text-[var(--banana)]'
              }`}
            >
              {mine ? '✓ Your vote' : votingFor === idea.idea_id ? 'Signing…' : me.my_vote?.round_date === round.round_date ? 'Move vote here' : 'Vote'}
            </button>
          ) : (
            <button type="button" onClick={onLogin} disabled={busy !== null}
              className="rounded-lg border border-[var(--border-strong)] px-3.5 py-1.5 font-mono text-[11.5px] uppercase tracking-[0.12em] text-[var(--dim)] hover:border-[var(--banana)] hover:text-[var(--banana)] disabled:opacity-60">
              Connect to vote
            </button>
          )
        ) : null}
      </div>
    </article>
  );
};

export const PoolSection: React.FC<Props> = ({ round, me, busy, errors, votingFor, onVote, onLogin }) => {
  const used = round?.slots.used ?? 0;
  const max = round?.slots.max ?? 0;
  const winnerId = round?.winner?.idea_id ?? null;
  return (
    <section id="pool" aria-labelledby="pool-title" className="min-w-0 scroll-mt-24">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-3">
        <h2 id="pool-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">Today&apos;s pool</h2>
        <span data-testid="slots" className="font-mono text-[12.5px] text-[var(--dim)]">
          <span className="tabular-nums text-[var(--fg-hi)]">{used}</span> / {max} ideas today
        </span>
      </div>

      {!round && <div role="status" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">Loading the pool…</div>}

      {round && !round.pool_visible && (
        <div data-testid="pool-hidden" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)] leading-relaxed">
          The pool opens when submissions close at {String(round.schedule.submit_closes_hour_utc).padStart(2, '0')}:00 UTC. Ideas stay hidden until then, so nobody can copy an idea they have already seen.
          Every idea is reviewed by the team first.
        </div>
      )}

      {round?.pool_visible && round.ideas.length === 0 && (
        <div data-testid="pool-empty" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">
          No idea passed review for this round, so there is nothing to vote on.
        </div>
      )}

      {round?.pool_visible && round.ideas.length > 0 && (
        <>
          {errors.vote && (
            <div role="alert" data-testid="vote-error" className="mb-3 rounded-xl border border-[var(--stall)]/60 px-4 py-2 text-[13px] text-[var(--stall)]">
              {errors.vote}
            </div>
          )}
          {!me?.wallet && errors.login && (
            <p data-testid="connect-hint" className="mb-3 text-[12.5px] text-[var(--stall)]">
              Could not connect your wallet. The reason is under <a href="#submit" className="underline">Connect your wallet</a> below.
            </p>
          )}
          {(round.phase === 'vote' || round.dev_open) && (
            <p className="mb-3 font-mono text-[11.5px] text-[var(--dim)] leading-relaxed">
              Hold at least {round.rules.vote_min_epc.toLocaleString('en-US')} EPC in a wallet older than {round.rules.vote_min_wallet_age_days} days. One vote per wallet, movable until voting closes. A signature, no gas.
            </p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {round.ideas.map((i) => (
              <IdeaCard key={i.idea_id} idea={i} round={round} me={me} busy={busy} votingFor={votingFor} isWinner={i.idea_id === winnerId}
                onVote={onVote} onLogin={onLogin} />
            ))}
          </div>
        </>
      )}
    </section>
  );
};
