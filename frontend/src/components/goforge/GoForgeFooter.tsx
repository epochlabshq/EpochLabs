import React from 'react';
import { getApiBaseUrl } from '@/config/constants';
import { BLOCKSCOUT_BASE, GOFORGE_ADDRESSES, GOFORGE_DISCLAIMER } from '@/config/goforgeCopy';

const ROWS = [
  { label: 'EpochLauncher', note: 'launches every GoForge token', address: GOFORGE_ADDRESSES.launcher },
  { label: 'Agent key', note: 'EIP-712 signer for Golem launches', address: GOFORGE_ADDRESSES.agent },
  { label: 'Golem wallet', note: 'holds the funds', address: GOFORGE_ADDRESSES.wallet },
];

export const GoForgeFooter: React.FC = () => (
  <section className="border-t border-[var(--rule)] bg-[var(--panel2)]">
    <div className="max-w-[1180px] mx-auto px-4 md:px-12 py-10 space-y-6">
      <p className="text-[var(--fg)] text-[14px] max-w-[60ch]">{GOFORGE_DISCLAIMER}</p>
      <dl className="grid grid-cols-1 md:grid-cols-3 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden">
        {ROWS.map((r) => (
          <div key={r.label} className="bg-[var(--panel)] px-4 py-3 min-w-0">
            <dt className="font-mono text-[10.5px] uppercase tracking-wider text-[var(--faint)]">
              {r.label} <span className="normal-case tracking-normal">· {r.note}</span>
            </dt>
            <dd className="mt-1 font-mono text-[12px] break-all">
              <a href={`${BLOCKSCOUT_BASE}/address/${r.address}`} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
                {r.address}
              </a>
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11.5px]">
        <a href={`${getApiBaseUrl()}/api/goforge`} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
          /api/goforge
        </a>
      </div>
    </div>
  </section>
);
