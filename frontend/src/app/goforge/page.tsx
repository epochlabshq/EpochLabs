'use client';

import React from 'react';
import { HeaderBar } from '@/components/layout/HeaderBar';
import { FooterBar } from '@/components/layout/FooterBar';
import { ComingSoonPanel } from '@/components/goforge/ComingSoon';
import { ForgedSection } from '@/components/goforge/ForgedSection';
import { GfHero } from '@/components/goforge/GfHero';
import { GoForgeFooter } from '@/components/goforge/GoForgeFooter';
import { HowItWorks } from '@/components/goforge/HowItWorks';
import { PoolSection } from '@/components/goforge/PoolSection';
import { ScoreboardSection } from '@/components/goforge/ScoreboardSection';
import { SubmitSection } from '@/components/goforge/SubmitSection';
import { Web3Provider } from '@/components/goforge/Web3Provider';
import { PHASE_LABEL } from '@/lib/gf';
import { GOFORGE_LIVE } from '@/config/goforgeCopy';
import { useEmileDatabase } from '@/hooks/useEmileDatabase';
import { useGfActions } from '@/hooks/useGfActions';
import { useGoForge } from '@/hooks/useGoForge';
import { useEmileStore } from '@/store/useEmileStore';

/** The live page body. It sits inside the wallet provider, so the wallet hooks (RainbowKit + wagmi) are available. */
const LiveContent: React.FC<{ refreshMe: () => Promise<void> }> = ({ refreshMe }) => {
  const actions = useGfActions(refreshMe);
  const round = useEmileStore((s) => s.gfRound);
  const roundError = useEmileStore((s) => s.gfRoundError);
  const launches = useEmileStore((s) => s.gfLaunches);
  const me = useEmileStore((s) => s.gfMe);
  return (
    <>
      {roundError && !round && (
        <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">
          GoForge data unavailable. Nothing is shown unless it can be read from /api/goforge.
        </div>
      )}
      {roundError && round && (
        <div role="status" className="font-mono text-[11.5px] text-[var(--stall)]">Could not refresh GoForge data. Showing the last read.</div>
      )}
      {round?.winner && (
        <div data-testid="winner-banner" className="rounded-xl border border-[var(--live)] bg-[var(--live-glow)] px-4 py-3 text-[14px] text-[var(--fg-hi)]">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--live)]">Winner · {round.round_date}</span>
          <div className="mt-1">
            <span className="font-semibold">{round.winner.name}</span> <span className="font-mono text-[var(--banana)]">{round.winner.ticker}</span>
            {round.winner.creator_handle && <> by @{round.winner.creator_handle}</>}
            <span className="text-[var(--dim)]"> · Golem launches it on Pons within {round.schedule.launch_window_hours}h.</span>
          </div>
        </div>
      )}
      <PoolSection round={round} me={me} busy={actions.busy} errors={actions.errors} votingFor={actions.votingFor} onVote={actions.vote} onLogin={actions.login} />
      <SubmitSection
        round={round} busy={actions.busy} actionErrors={actions.errors} connected={actions.connected} address={actions.address}
        onLogin={actions.login} onLogout={actions.logout} onPayFee={actions.payFee} onSubmit={actions.submit}
      />
      <ScoreboardSection round={round} />
      <ForgedSection data={launches} />
      <HowItWorks round={round} />
    </>
  );
};

export default function GoForgePage() {
  useEmileDatabase(); // WebSocket: gf_vote, gf_phase, gf_winner, gf_launch, gf_verdict, goforge_*
  const { refreshMe } = useGoForge(GOFORGE_LIVE);
  const round = useEmileStore((s) => s.gfRound);

  const phase = !GOFORGE_LIVE ? 'coming soon' : round ? PHASE_LABEL[round.phase].toLowerCase() : '';

  return (
    <div className="wrap min-h-screen flex flex-col overflow-x-clip">
      <HeaderBar phaseText={phase ? `goforge · ${phase}` : 'goforge'} />
      <main className="flex-1">
        <GfHero round={round} soon={!GOFORGE_LIVE} />

        <div className="max-w-[1180px] mx-auto w-full px-4 md:px-12 py-8 md:py-12 space-y-10 md:space-y-12">
          {!GOFORGE_LIVE && <ComingSoonPanel />}
          {GOFORGE_LIVE && (
            <Web3Provider>
              <LiveContent refreshMe={refreshMe} />
            </Web3Provider>
          )}
        </div>

        <GoForgeFooter />
      </main>
      <FooterBar />
    </div>
  );
}
