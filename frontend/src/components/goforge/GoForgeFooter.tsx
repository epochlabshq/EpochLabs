import React from 'react';
import { GOFORGE_DISCLAIMER } from '@/config/goforgeCopy';

export const GoForgeFooter: React.FC = () => (
  <section className="border-t border-[var(--rule)] bg-[var(--panel2)]">
    <div className="max-w-[1180px] mx-auto px-4 md:px-12 py-10 space-y-5">
      <p className="text-[var(--fg)] text-[14px] max-w-[64ch]">{GOFORGE_DISCLAIMER}</p>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11.5px]">
        <a href="/api/goforge/round" target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">/api/goforge/round</a>
        <a href="/api/goforge/launches" target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">/api/goforge/launches</a>
      </div>
    </div>
  </section>
);
