'use client';

import React, { useCallback, useState } from 'react';
import { HeaderBar } from '@/components/layout/HeaderBar';
import { FooterBar } from '@/components/layout/FooterBar';
import { StatusBar } from '@/components/desk/StatusBar';
import { WaitingPanel, WatchingPanel } from '@/components/desk/WatchingPanel';
import { ClosedTrades, OpenPositions } from '@/components/desk/TradesPanels';
import { WhyCardDialog } from '@/components/desk/WhyCard';
import { DeskFooter } from '@/components/desk/DeskFooter';
import { DESK_COMING_SOON, DESK_HERO, DESK_STATE_LABEL } from '@/config/deskCopy';
import { useEmileDatabase } from '@/hooks/useEmileDatabase';
import { useDesk } from '@/hooks/useDesk';
import { useEmileStore } from '@/store/useEmileStore';

export default function DeskPage() {
  useEmileDatabase(); // WebSocket: desk_state, desk_waiting, desk_open, desk_close
  useDesk();
  const data = useEmileStore((s) => s.desk);
  const error = useEmileStore((s) => s.deskError);
  const [whyId, setWhyId] = useState<string | null>(null);
  const closeWhy = useCallback(() => setWhyId(null), []);

  return (
    <div className="wrap min-h-screen flex flex-col overflow-x-clip">
      <HeaderBar phaseText={data ? `desk · ${DESK_STATE_LABEL[data.state].toLowerCase()}` : 'desk'} />
      {data && <StatusBar data={data} />}
      <main className="flex-1">
        <section className="relative overflow-hidden border-b border-[var(--rule)]">
          <div aria-hidden className="pointer-events-none absolute inset-0 epochs-grid" />
          <div className="relative max-w-[1180px] mx-auto px-4 md:px-12 pt-10 pb-8 md:pt-14 md:pb-10 grid grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-8 lg:gap-12 items-center">
            <div className="min-w-0">
              <div className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[var(--banana)] flex items-center gap-3">
                <span className="w-8 h-px bg-[var(--banana)]" />
                Epoch Labs · Live
              </div>
              <h1 className="font-sans font-semibold text-5xl md:text-7xl tracking-[-0.045em] leading-[0.95] mt-4 text-[var(--fg-hi)]">
                {DESK_HERO.title}
              </h1>
              <p className="font-serif italic text-2xl md:text-[2rem] text-[var(--banana)] mt-2">{DESK_HERO.subtitle}</p>
              <p className="text-[var(--dim)] text-[15px] md:text-base max-w-[56ch] mt-4 leading-relaxed">{DESK_HERO.intro}</p>
            </div>

            {/* Until Golem may trade, the desk only watches: say so where the trading would be */}
            {data?.state === 'gated' && (
              <div className="relative min-w-0 rounded-2xl border border-dashed border-[var(--banana)]/50 bg-[var(--panel)]/70 backdrop-blur px-6 py-7 md:px-8 md:py-9 text-center overflow-hidden">
                <div aria-hidden className="absolute inset-0 epochs-bloom opacity-40 pointer-events-none" />
                <div className="relative">
                  <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[var(--banana)]/60 font-mono text-[10.5px] uppercase tracking-[0.25em] text-[var(--banana)]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--banana)] epochs-pulse" />
                    {DESK_COMING_SOON.badge}
                  </div>
                  <p className="font-sans font-semibold text-3xl md:text-4xl tracking-tight text-[var(--fg-hi)] mt-4">
                    {DESK_COMING_SOON.title}
                  </p>
                  <p className="text-[var(--dim)] text-[14px] mt-3 max-w-[40ch] mx-auto leading-relaxed">{DESK_COMING_SOON.body}</p>
                </div>
              </div>
            )}
          </div>
        </section>

        <div className="max-w-[1180px] mx-auto w-full px-4 md:px-12 py-8 md:py-12 space-y-10 md:space-y-12">
          {!data && (
            <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">
              {error ? 'Desk data unavailable. Nothing is shown unless it can be read from /api/desk.' : 'Loading the desk…'}
            </div>
          )}

          {data && (
            <>
              {error && (
                <div role="status" className="font-mono text-[11.5px] text-[var(--stall)]">
                  Could not refresh desk data. Showing the last read.
                </div>
              )}
              <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] gap-10 xl:gap-8 items-start">
                <WatchingPanel data={data} />
                <WaitingPanel data={data} />
              </div>
              <OpenPositions data={data} onWhy={setWhyId} />
              <ClosedTrades data={data} onWhy={setWhyId} />
            </>
          )}
        </div>

        <DeskFooter data={data} />
      </main>
      <FooterBar />
      <WhyCardDialog tradeId={whyId} onClose={closeWhy} />
    </div>
  );
}
