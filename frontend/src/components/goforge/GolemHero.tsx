'use client';

import React, { useEffect, useRef } from 'react';

/**
 * The hero clip on a retro TV: Golem forging a coin on its anvil. The set has a bezel, a glass screen with scanlines,
 * a vignette and a reflection, a power light, knobs and feet. The clip plays muted on a loop, pauses while off screen,
 * and stays on the poster frame when the visitor prefers reduced motion.
 */
export const GolemHero: React.FC<{ className?: string }> = ({ className }) => {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) v.play().catch(() => {});
      else v.pause();
    });
    io.observe(v);
    return () => io.disconnect();
  }, []);

  return (
    <div className={`relative ${className ?? ''}`}>
      {/* forge glow behind the set */}
      <div aria-hidden className="pointer-events-none absolute -inset-10 bg-[radial-gradient(closest-side,rgba(233,159,48,.32),rgba(233,159,48,.08)_60%,transparent)] blur-2xl" />

      <div
        data-testid="tv-frame"
        className="relative rounded-[1.75rem] p-3 sm:p-4 border border-[#7a5224] shadow-[0_28px_60px_rgba(0,0,0,.55),inset_0_2px_0_rgba(255,214,150,.22),inset_0_-3px_0_rgba(0,0,0,.45)]"
        style={{ background: 'linear-gradient(160deg, #5b3d1c 0%, #3b2611 45%, #2a1a0b 100%)' }}
      >
        {/* screen */}
        <div className="relative overflow-hidden rounded-[1.25rem] ring-[3px] ring-black/70 shadow-[inset_0_0_30px_rgba(0,0,0,.6)] bg-[#1a1008]">
          <video
            ref={ref}
            src="/videos/golem-forging.mp4"
            poster="/videos/golem-forging-poster.jpg"
            muted
            loop
            playsInline
            preload="metadata"
            aria-label="Golem forging a coin on its anvil"
            className="block w-full h-auto aspect-video object-cover [filter:saturate(1.12)_contrast(1.08)_brightness(.94)]"
          />
          {/* warm phosphor tint */}
          <div aria-hidden className="pointer-events-none absolute inset-0 mix-blend-multiply" style={{ background: 'linear-gradient(180deg, rgba(250,200,130,.7), rgba(215,145,65,.8))' }} />
          {/* scanlines */}
          <div aria-hidden className="pointer-events-none absolute inset-0 opacity-35" style={{ background: 'repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0px, rgba(0,0,0,.16) 1px, transparent 1px, transparent 3px)' }} />
          {/* tube vignette */}
          <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: 'radial-gradient(ellipse 85% 85% at 50% 50%, transparent 55%, rgba(20,10,2,.55) 100%)' }} />
          {/* glass reflection */}
          <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: 'linear-gradient(115deg, rgba(255,255,255,.16) 0%, rgba(255,255,255,.04) 28%, transparent 40%)' }} />
        </div>

        {/* control strip */}
        <div className="mt-3 flex items-center justify-between gap-3 px-1">
          <div className="flex items-center gap-2 font-mono text-[9.5px] uppercase tracking-[0.25em] text-[#c99a5a]">
            <span aria-hidden className="w-2 h-2 rounded-full bg-[#ffb340] epochs-pulse" />
            GoForge Labs · Forging
          </div>
          <div aria-hidden className="flex-1 max-w-[88px] grid grid-cols-8 gap-[3px]">
            {Array.from({ length: 16 }).map((_, i) => (
              <span key={i} className="h-[3px] rounded-full bg-black/55" />
            ))}
          </div>
          <div aria-hidden className="flex items-center gap-2">
            {[0, 1].map((k) => (
              <span
                key={k}
                className="w-4 h-4 rounded-full border border-black/60 shadow-[inset_0_1px_0_rgba(255,214,150,.35)]"
                style={{ background: 'radial-gradient(circle at 35% 30%, #d9a45a, #6f4a1f)' }}
              />
            ))}
          </div>
        </div>
      </div>

      {/* feet */}
      <div aria-hidden className="flex justify-between px-[12%] -mt-px">
        <span className="w-10 h-2 rounded-b-md bg-[#2a1a0b] border border-t-0 border-[#7a5224]" />
        <span className="w-10 h-2 rounded-b-md bg-[#2a1a0b] border border-t-0 border-[#7a5224]" />
      </div>
    </div>
  );
};
