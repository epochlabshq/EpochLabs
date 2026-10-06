'use client';

import React, { useEffect, useState } from 'react';
import { NO_LAUNCH_TEXT } from '@/lib/gf';
import { gfApi } from '@/lib/gfApi';
import { useEmileStore } from '@/store/useEmileStore';
import { CopyButton } from './CopyButton';
import type { RoundPayload, ScoreRow, Scoreboard } from './types';

const Num: React.FC<{ v: number; strong?: boolean }> = ({ v, strong }) => (
  <span className={`font-mono tabular-nums ${strong ? 'text-[var(--fg-hi)] font-semibold' : 'text-[var(--fg)]'}`}>{v.toFixed(2)}</span>
);

const Thumb: React.FC<{ row: ScoreRow }> = ({ row }) => (
  // eslint-disable-next-line @next/next/no-img-element
  <img src={row.image_url} alt="" width={36} height={36} loading="lazy" className="w-9 h-9 rounded-lg object-cover border border-[var(--border-strong)] bg-[var(--panel2)] shrink-0" />
);

const WinnerMark: React.FC = () => (
  <span className="rounded-md border border-[var(--live)] bg-[var(--live-glow)] px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.12em] text-[var(--live)]">✓ Winner</span>
);

export const ScoreboardSection: React.FC<{ round: RoundPayload | null }> = ({ round }) => {
  const version = useEmileStore((s) => s.gfVersion);
  const [fetched, setBoard] = useState<Scoreboard | null>(null);
  const [failed, setFailed] = useState(false);
  // today's table from 20:00 UTC; before that, the last finished round
  const date = round ? (round.scoreboard_available ? round.round_date : round.last_round?.round_date ?? null) : null;
  const board = date ? fetched : null;

  useEffect(() => {
    if (!date) return;
    let cancelled = false;
    gfApi.scoreboard(date).then((b) => { if (!cancelled) { setBoard(b); setFailed(false); } }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [date, version]);

  return (
    <section id="scoreboard" aria-labelledby="scoreboard-title" className="min-w-0 scroll-mt-24">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-3">
        <h2 id="scoreboard-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">
          Scoreboard
          {board && <span className="ml-2 font-mono text-[13px] font-normal text-[var(--faint)]">{board.round_date}</span>}
        </h2>
        {board && (
          <a href={gfApi.votesUrl(board.round_date)} target="_blank" rel="noopener noreferrer" className="font-mono text-[12px] text-[var(--banana)] hover:underline">
            Audit every vote →
          </a>
        )}
      </div>

      {!board && (
        <div data-testid="scoreboard-empty" role="status" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)] leading-relaxed">
          {failed
            ? 'The scoreboard could not be read right now.'
            : <>The full table of every idea, with its credibility, Golem, vote and final score, is published when voting closes at {String(round?.schedule.vote_closes_hour_utc ?? 20).padStart(2, '0')}:00 UTC.</>}
        </div>
      )}

      {board && (
        <>
          {board.no_launch_reason && (
            <p data-testid="no-launch" className="mb-3 rounded-xl border border-[var(--border-strong)] bg-[var(--panel)] p-4 text-[14px] text-[var(--fg)]">
              No launch for this round. {NO_LAUNCH_TEXT[board.no_launch_reason] ?? ''}
            </p>
          )}
          {board.rows.length === 0 ? (
            <p className="text-[14px] text-[var(--dim)]">No idea was approved for this round.</p>
          ) : (
            <>
              {/* desktop table */}
              <div className="hidden md:block rounded-xl border border-[var(--border)] overflow-hidden">
                <table data-testid="scoreboard" className="w-full text-[13px]">
                  <thead className="bg-[var(--panel2)] font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--faint)]">
                    <tr>
                      <th scope="col" className="text-left px-3 py-2 w-10">#</th>
                      <th scope="col" className="text-left px-3 py-2">Idea</th>
                      <th scope="col" className="text-right px-3 py-2">Credibility</th>
                      <th scope="col" className="text-right px-3 py-2">Golem</th>
                      <th scope="col" className="text-right px-3 py-2">Vote</th>
                      <th scope="col" className="text-right px-3 py-2">Votes</th>
                      <th scope="col" className="text-right px-3 py-2">Final</th>
                    </tr>
                  </thead>
                  <tbody>
                    {board.rows.map((r) => (
                      <tr key={r.idea_id} data-testid="score-row" data-winner={r.winner} className={`border-t border-[var(--rule)] ${r.winner ? 'bg-[var(--live-glow)]' : ''}`}>
                        <td className="px-3 py-2 font-mono text-[var(--dim)]">{r.rank}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-3 min-w-0">
                            <Thumb row={r} />
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2"><span className="text-[var(--fg-hi)] font-medium">{r.name}</span><span className="font-mono text-[11.5px] text-[var(--banana)]">{r.ticker}</span>{r.winner && <WinnerMark />}</div>
                              {r.x_handle && <div className="font-mono text-[11px] text-[var(--dim)]">@{r.x_handle}</div>}
                            </div>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right"><Num v={r.credibility} /></td>
                        <td className="px-3 py-2 text-right"><Num v={r.golem} /></td>
                        <td className="px-3 py-2 text-right"><Num v={r.vote} /></td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--dim)]">{r.votes}</td>
                        <td className="px-3 py-2 text-right"><Num v={r.final} strong /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* mobile cards */}
              <ul className="md:hidden space-y-3">
                {board.rows.map((r) => (
                  <li key={r.idea_id} data-testid="score-card" className={`rounded-xl border p-3 ${r.winner ? 'border-[var(--live)] bg-[var(--live-glow)]' : 'border-[var(--border)] bg-[var(--panel)]'}`}>
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="font-mono text-[var(--dim)] w-5">{r.rank}</span>
                      <Thumb row={r} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2"><span className="text-[var(--fg-hi)] font-medium break-words">{r.name}</span><span className="font-mono text-[11.5px] text-[var(--banana)]">{r.ticker}</span></div>
                        {r.x_handle && <div className="font-mono text-[11px] text-[var(--dim)]">@{r.x_handle}</div>}
                      </div>
                      {r.winner && <WinnerMark />}
                    </div>
                    <dl className="mt-3 grid grid-cols-4 gap-2 font-mono text-[11px]">
                      {([['Cred.', r.credibility], ['Golem', r.golem], ['Vote', r.vote], ['Final', r.final]] as [string, number][]).map(([k, v]) => (
                        <div key={k}><dt className="text-[var(--faint)] uppercase tracking-[0.1em] text-[9.5px]">{k}</dt><dd className="mt-0.5 text-[var(--fg-hi)] tabular-nums">{v.toFixed(2)}</dd></div>
                      ))}
                    </dl>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="mt-3 font-mono text-[11px] text-[var(--faint)] leading-relaxed space-y-1">
            <div>{board.formula}. Components are on a 0-100 scale; the final score is computed from the rounded numbers shown here.</div>
            {board.scoreboard_sha256 && (
              <div className="flex flex-wrap items-center gap-2">
                <span>table sha256</span>
                <code className="break-all text-[var(--dim)]">{board.scoreboard_sha256}</code>
                <CopyButton value={board.scoreboard_sha256} label="scoreboard hash" />
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
};
