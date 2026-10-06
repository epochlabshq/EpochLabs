import React from 'react';
import { MODERATION_RULES, TEAM_RULE } from '@/config/goforgeCopy';
import type { RoundPayload } from './types';

const pad = (h: number) => `${String(h).padStart(2, '0')}:00`;

const Block: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 min-w-0">
    <h3 className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--banana)]">{title}</h3>
    <div className="mt-2 text-[13.5px] leading-relaxed text-[var(--fg)] space-y-2">{children}</div>
  </div>
);

const Row: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
  <div className="flex items-baseline justify-between gap-4 border-b border-[var(--rule)] last:border-0 py-1.5">
    <span className="text-[var(--dim)]">{k}</span>
    <span className="font-mono text-[12.5px] text-[var(--fg-hi)] text-right">{v}</span>
  </div>
);

/** The rules in full. Every number is read from /api/goforge/round, so this page cannot drift from the backend. */
export const HowItWorks: React.FC<{ round: RoundPayload | null }> = ({ round }) => {
  const r = round?.rules;
  const s = round?.schedule;
  return (
    <section id="how" aria-labelledby="how-title" className="min-w-0 scroll-mt-24">
      <h2 id="how-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] mb-3">How it works</h2>
      {!r || !s ? (
        <p role="status" className="text-[14px] text-[var(--dim)]">Loading the rules…</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Block title="Every day, in UTC">
            <Row k="Submit opens" v={pad(s.submit_opens_hour_utc)} />
            <Row k="Submit closes, voting opens" v={pad(s.submit_closes_hour_utc)} />
            <Row k="Voting closes, scoring" v={pad(s.vote_closes_hour_utc)} />
            <Row k="Winner announced on X" v={`${pad(s.vote_closes_hour_utc).slice(0, 3)}${String(s.announce_minute_utc).padStart(2, '0')}`} />
            <Row k="Launch on Pons" v={`within ${s.launch_window_hours}h`} />
            <Row k="Verdict" v="launch + 48h" />
            <p className="text-[12.5px] text-[var(--dim)]">Golem picks the launch hour from the highest survival window in its own data. At most {r.max_ideas_per_day} ideas a day.</p>
          </Block>

          <Block title="Scoring">
            <p className="font-mono text-[12.5px] text-[var(--fg-hi)]">
              final = {r.weights.credibility.toFixed(2)} × credibility + {r.weights.golem.toFixed(2)} × golem + {r.weights.vote.toFixed(2)} × vote
            </p>
            <p><span className="text-[var(--fg-hi)]">Credibility (40%)</span>: verified X account, account age (full marks at 2 years), followers (full marks at 100K, log scale), and GoForge track record (50 to start, +25 for every earlier win that reached $30K, −15 for every one that stalled).</p>
            <p><span className="text-[var(--fg-hi)]">Golem (30%)</span>: how often the idea&apos;s narrative survives on Meta Radar compared with the chain baseline (20/30), and lore quality (10/30). A saturated narrative loses 20 points; a thin sample is neutral.</p>
            <p><span className="text-[var(--fg-hi)]">Vote (30%)</span>: votes for the idea divided by the votes of the most-voted idea that day.</p>
            <p className="text-[12.5px] text-[var(--dim)]">Ties go to the earlier submission. One creator cannot win more than once in {r.win_cooldown_days} days. With no approved idea or no vote, there is no launch that day, and the reason is posted.</p>
          </Block>

          <Block title="Voting">
            <Row k="Hold at least" v={`${r.vote_min_epc.toLocaleString('en-US')} EPC`} />
            <Row k="Wallet age at least" v={`${r.vote_min_wallet_age_days} days`} />
            <Row k="Votes per wallet" v="1 a day, movable" />
            <Row k="Vote changes" v={`${r.vote_changes_per_hour} per hour`} />
            <p className="text-[12.5px] text-[var(--dim)]">A vote is an EIP-712 signature, no gas. The balance is checked when you vote and again when voting closes; a wallet that no longer holds the minimum does not count. You cannot vote for your own idea. Every vote and signature is published after the close.</p>
          </Block>

          <Block title="Submit fee and creator fees">
            <Row k="Submit fee" v={`${r.submit_fee_epc.toLocaleString('en-US')} EPC, burned`} />
            <Row k="Creator fees" v="50% creator · 50% EPC buyback & burn" />
            <p className="text-[12.5px] text-[var(--dim)]">The fee is sent straight to the burn address and is not refunded, even if the idea is rejected. Creator fees of the launched token go to a splitter contract with no owner and no withdraw function: it pays the creator&apos;s verified wallet and buys EPC for the burn address. Every distribution is listed with its transaction.</p>
          </Block>

          <Block title="Identity">
            <p>Your wallet comes from a signed-in message (SIWE) and your X account from X&apos;s own login. Neither can be typed in, so nobody can submit under someone else&apos;s handle or redirect their fees. One X account links to one wallet.</p>
            <p className="text-[12.5px] text-[var(--dim)]">{TEAM_RULE}</p>
          </Block>

          <Block title="Moderation">
            <ul className="list-disc pl-5 space-y-1 marker:text-[var(--banana)]">
              {MODERATION_RULES.map((m) => <li key={m}>{m}</li>)}
            </ul>
          </Block>
        </div>
      )}
    </section>
  );
};
