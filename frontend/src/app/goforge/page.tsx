'use client';

import React, { useCallback, useState } from 'react';
import { HeaderBar } from '@/components/layout/HeaderBar';
import { FooterBar } from '@/components/layout/FooterBar';
import { ComingSoonPanel, ComingSoonPill } from '@/components/goforge/ComingSoon';
import { GoForgeFooter } from '@/components/goforge/GoForgeFooter';
import { GolemHero } from '@/components/goforge/GolemHero';
import { GateBadge, LaunchGatePanel } from '@/components/goforge/LaunchGate';
import { LaunchCard } from '@/components/goforge/LaunchCard';
import { TotalsBar } from '@/components/goforge/TotalsBar';
import { WhyDialog } from '@/components/goforge/WhyDialog';
import type { Launch } from '@/components/goforge/types';
import { GATE_LABEL, GOFORGE_EMPTY, GOFORGE_HERO, GOFORGE_LIVE } from '@/config/goforgeCopy';
import { useEmileDatabase } from '@/hooks/useEmileDatabase';
import { useGoForge } from '@/hooks/useGoForge';
import { useEmileStore } from '@/store/useEmileStore';

export default function GoForgePage() {
  useEmileDatabase(); // WebSocket: goforge_update, goforge_verdict, goforge_burn
  useGoForge(GOFORGE_LIVE);
  const data = useEmileStore((s) => s.goforge);
  const error = useEmileStore((s) => s.goforgeError);
  const flashId = useEmileStore((s) => s.goforgeFlashId);
  const [whyId, setWhyId] = useState<string | null>(null);
  const closeWhy = useCallback(() => setWhyId(null), []);
  const openWhy = useCallback((l: Launch) => setWhyId(l.id), []);
  // Resolve from the store so a live update refreshes the open dialog
  const whyLaunch = data?.launches.find((l) => l.id === whyId) ?? null;

  const gateStatus = data?.gate?.status ?? null;

  return (
    <div className="wrap min-h-screen flex flex-col overflow-x-clip">
      <HeaderBar phaseText={!GOFORGE_LIVE ? 'goforge · coming soon' : data ? `goforge · ${gateStatus ? GATE_LABEL[gateStatus].toLowerCase() : 'gate unavailable'}` : 'goforge'} />
      <main className="flex-1">
        <section className="relative overflow-hidden border-b border-[var(--rule)]">
          <div aria-hidden className="pointer-events-none absolute inset-0 epochs-grid" />
          <div aria-hidden className="pointer-events-none absolute right-[-10%] top-[-10%] w-[70%] h-[120%] bg-[radial-gradient(closest-side,rgba(233,159,48,.22),transparent)]" />
          <div className="relative max-w-[1180px] mx-auto px-4 md:px-12 pt-10 pb-6 md:pt-14 md:pb-8 grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-6 lg:gap-10 items-center">
            <div className="min-w-0">
              <div className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[var(--banana)] flex items-center gap-3">
                <span className="w-8 h-px bg-[var(--banana)]" />
                Epoch Labs · Launches
              </div>
              <h1 className="font-sans font-semibold text-5xl md:text-7xl tracking-[-0.045em] leading-[0.95] mt-4 text-[var(--fg-hi)]">
                {GOFORGE_HERO.title}
              </h1>
              <p className="font-serif italic text-2xl md:text-[2rem] text-[var(--banana)] mt-2">{GOFORGE_HERO.subtitle}</p>
              <p className="text-[var(--dim)] text-[15px] md:text-base max-w-[56ch] mt-4 leading-relaxed">{GOFORGE_HERO.intro}</p>
              <div className="mt-6">{!GOFORGE_LIVE ? <ComingSoonPill /> : data ? <GateBadge status={gateStatus} /> : null}</div>
            </div>
            <GolemHero className="w-full" />
          </div>
        </section>

        <div className="max-w-[1180px] mx-auto w-full px-4 md:px-12 py-8 md:py-12 space-y-10 md:space-y-12">
          {!GOFORGE_LIVE && <ComingSoonPanel />}

          {GOFORGE_LIVE && !data && (
            <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-6 font-mono text-[13px] text-[var(--dim)]">
              {error ? 'GoForge data unavailable. Nothing is shown unless it can be read from /api/goforge.' : 'Loading GoForge…'}
            </div>
          )}

          {GOFORGE_LIVE && data && (
            <>
              {error && (
                <div role="status" className="font-mono text-[11.5px] text-[var(--stall)]">
                  Could not refresh GoForge data. Showing the last read.
                </div>
              )}
              <LaunchGatePanel gate={data.gate} />
              <TotalsBar totals={data.totals} />
              <section aria-labelledby="launched-title" className="min-w-0">
                <h2 id="launched-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] mb-3">
                  Launched tokens
                  <span className="ml-2 font-mono text-[13px] font-normal text-[var(--faint)]">{data.launches.length}</span>
                </h2>
                {data.launches.length === 0 ? (
                  <div data-testid="empty-state" className="rounded-xl border border-dashed border-[var(--border-strong)] p-5 text-[14px] text-[var(--dim)]">
                    {GOFORGE_EMPTY}
                  </div>
                ) : (
                  <div className="space-y-4">
                    {data.launches.map((l) => (
                      <LaunchCard key={l.id} launch={l} flash={flashId === l.id} onWhy={openWhy} />
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>

        <GoForgeFooter />
      </main>
      <FooterBar />
      <WhyDialog launch={whyLaunch} onClose={closeWhy} />
    </div>
  );
}
