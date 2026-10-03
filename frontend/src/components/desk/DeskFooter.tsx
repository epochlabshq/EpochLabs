import React from 'react';
import { getApiBaseUrl } from '@/config/constants';
import { DESK_COPY } from '@/config/deskCopy';
import type { DeskPayload } from './types';

export const DeskFooter: React.FC<{ data: DeskPayload | null }> = ({ data }) => {
  const rows = data
    ? [
        { label: 'Golem wallet', note: 'holds the funds and sends every swap', link: data.wallet },
        { label: 'Agent key', note: 'EIP-712 signer for Golem launches', link: data.agent },
        ...(data.epc_burned.burn_address ? [{ label: 'Burn address', note: 'EPC burns', link: data.epc_burned.burn_address }] : []),
      ]
    : [];

  return (
    <section className="border-t border-[var(--rule)] bg-[var(--panel2)]">
      <div className="max-w-[1180px] mx-auto px-4 md:px-12 py-10 space-y-6">
        <p className="text-[var(--fg)] text-[14px] max-w-[60ch]">{DESK_COPY.footer}</p>

        {rows.length > 0 && (
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-px bg-[var(--rule)] border border-[var(--rule)] rounded-xl overflow-hidden">
            {rows.map((r) => (
              <div key={r.label} className="bg-[var(--panel)] px-4 py-3 min-w-0">
                <dt className="font-mono text-[10.5px] uppercase tracking-wider text-[var(--faint)]">
                  {r.label} <span className="normal-case tracking-normal">· {r.note}</span>
                </dt>
                <dd className="mt-1 font-mono text-[12px] break-all">
                  {r.link.url ? (
                    <a href={r.link.url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
                      {r.link.address}
                    </a>
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11.5px]">
          <a href={`${getApiBaseUrl()}/api/desk`} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
            /api/desk
          </a>
          {data?.wallet.url && (
            <a href={data.wallet.url} target="_blank" rel="noopener noreferrer" className="text-[var(--banana)] hover:underline">
              Blockscout
            </a>
          )}
        </div>
      </div>
    </section>
  );
};
