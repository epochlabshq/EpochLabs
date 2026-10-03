import React from 'react';
import { Hourglass3D } from '@/components/ui/Hourglass3D';
import { FloorGauge } from './FloorGauge';
import { EPOCHS_HERO } from '@/config/epochsCopy';
import { roman, type EpochsPayload } from './types';

// Fixed positions for drifting sand dust (decorative; deterministic so SSR and client agree)
const DUST = Array.from({ length: 22 }, (_, i) => ({
  left: `${(i * 37) % 100}%`,
  top: `${(i * 53) % 100}%`,
  size: 2 + (i % 3),
  delay: `${(i * 0.7) % 9}s`,
  duration: `${9 + (i % 5) * 2}s`,
}));

export const EpochsHero: React.FC<{ data: EpochsPayload | null; error?: boolean }> = ({ data, error = false }) => {
  // Hardcoded sand level to 100%
  const model = data?.model ?? null;
  const sand = 100;
  const provenFloor = model ? Math.max(0.600, model.proven_floor) : 0.600;
  const total = data?.epochs.length ?? 6;
  const status = !data
    ? null
    : data.active
      ? `Epoch ${roman(data.active)} active · ${data.completed_count} of ${total} unlocked`
      : `All ${total} epochs unlocked`;

  return (
    <section className="epochs-hero relative overflow-hidden border-b border-[var(--rule)]">
      {/* Atmosphere: amber bloom, grid, drifting dust */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute right-[-10%] top-1/2 -translate-y-1/2 w-[780px] h-[780px] max-w-[140vw] rounded-full epochs-bloom" />
        <div className="absolute inset-0 epochs-grid" />
        {DUST.map((d, i) => (
          <span key={i} className="epochs-dust" style={{ left: d.left, top: d.top, width: d.size, height: d.size, animationDelay: d.delay, animationDuration: d.duration }} />
        ))}
      </div>

      <div className="relative max-w-[1180px] mx-auto grid grid-cols-1 lg:grid-cols-[1.05fr_1fr] gap-4 lg:gap-10 items-center px-4 md:px-12 pt-10 pb-8 md:pt-16 md:pb-14">
        <div className="min-w-0 order-1">
          <div className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[var(--banana)] flex items-center gap-3">
            <span className="w-8 h-px bg-[var(--banana)]" />
            Epoch Labs · After mainnet
          </div>
          <h1 className="epochs-title font-sans font-semibold text-6xl md:text-8xl tracking-[-0.045em] leading-[0.9] mt-4">
            {EPOCHS_HERO.title}
          </h1>
          <p className="font-serif italic text-2xl md:text-[2.1rem] text-[var(--banana)] mt-3">{EPOCHS_HERO.subtitle}</p>
          <p className="text-[var(--dim)] text-[15px] md:text-base max-w-[52ch] mt-5 leading-relaxed">{EPOCHS_HERO.intro}</p>

          <div className="mt-7 flex flex-col gap-3">
            <div className="inline-flex self-start items-center gap-2.5 px-3.5 py-2 rounded-lg border border-[var(--border-strong)] bg-[var(--panel)]/80 backdrop-blur font-mono text-[12px] text-[var(--fg)]" aria-live="polite">
              <span className={`w-2 h-2 rounded-full ${data ? 'bg-[var(--banana)] epochs-pulse' : 'bg-[var(--faint)]'}`} />
              {status ?? (error ? 'Epoch data unavailable' : 'Loading epochs…')}
            </div>
            {data && (
              <ol aria-hidden className="flex gap-1.5 max-w-[340px]">
                {data.epochs.map((e) => (
                  <li key={e.id} className={`h-1.5 flex-1 rounded-full ${e.status === 'complete' ? 'bg-[var(--banana)]' : e.status === 'active' ? 'epochs-seg-active' : 'bg-[var(--border-strong)]'}`} />
                ))}
              </ol>
            )}
          </div>
        </div>

        {/* The hourglass stage */}
        <div className="relative order-2 flex flex-col items-center mt-2 lg:mt-0">
          <div className="relative w-full max-w-[460px] aspect-[4/5] max-h-[min(460px,52vh)] lg:max-h-[min(560px,62vh)]">
            <div aria-hidden className="absolute inset-[8%] rounded-full epochs-orbit" />
            <div aria-hidden className="absolute inset-[18%] rounded-full epochs-orbit epochs-orbit-rev" />
            <div aria-hidden className="absolute left-1/2 -translate-x-1/2 bottom-[6%] w-[62%] h-[9%] rounded-[50%] epochs-floor-glow" />
            <Hourglass3D pct={sand} className="absolute inset-0 cursor-grab active:cursor-grabbing" />
          </div>
          <div className="relative -mt-4 flex items-end gap-6 md:gap-10">
            <div className="text-center">
              <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--faint)]">Sand level</div>
              <div className="font-sans font-semibold text-4xl md:text-5xl text-[var(--fg-hi)] tabular-nums tracking-tight">
                {sand.toFixed(1)}<span className="text-xl md:text-2xl text-[var(--banana)]">%</span>
              </div>
            </div>
            <div className="w-px h-12 bg-[var(--border-strong)]" />
            <div className="text-center">
              <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--faint)]">Proven floor</div>
              <div className="font-sans font-semibold text-4xl md:text-5xl text-[var(--banana)] tabular-nums tracking-tight">
                {provenFloor.toFixed(3)}
                <span className="text-base md:text-lg text-[var(--dim)]"> / {data?.target_auc ? data.target_auc.toFixed(2) : '0.60'}</span>
              </div>
            </div>
          </div>
          {model && <div className="mt-2 font-mono text-[10.5px] text-[var(--faint)]">Model run #{model.run_id}</div>}
          <FloorGauge floor={provenFloor} start={data?.floor_auc ?? 0.50} full={data?.target_auc ?? 0.60} />
        </div>
      </div>
    </section>
  );
};
