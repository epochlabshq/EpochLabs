import React from 'react';
import { BLOCKSCOUT_BASE, RULES_PENDING } from '@/config/goforgeCopy';
import { fmtDateTime, fmtUsd, shortAddr, type HookRules as Rules } from './types';

const Addr: React.FC<{ address: string }> = ({ address }) => (
  <a
    href={`${BLOCKSCOUT_BASE}/address/${address}`}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(e) => e.stopPropagation()}
    className="text-[var(--banana)] hover:underline break-all"
  >
    {shortAddr(address)}
  </a>
);

const isDate = (s: string) => !Number.isNaN(new Date(s).getTime());

/** Only fields that have a value are listed: a null rule is not "0" or "off", it is simply not deployed yet. */
export function ruleRows(r: Rules): { label: string; value: React.ReactNode }[] {
  const rows: { label: string; value: React.ReactNode }[] = [];
  if (r.anti_snipe_blocks !== null) {
    rows.push({ label: 'Anti-snipe', value: `${r.anti_snipe_blocks} block${r.anti_snipe_blocks === 1 ? '' : 's'}` });
  }
  if (r.wallet_cap_pct !== null || r.wallet_cap_minutes !== null) {
    const cap = r.wallet_cap_pct !== null ? `${r.wallet_cap_pct}% of supply per wallet` : 'wallet cap';
    rows.push({ label: 'Wallet cap', value: r.wallet_cap_minutes !== null ? `${cap} for ${r.wallet_cap_minutes} min` : cap });
  }
  if (r.lp_lock_until !== null) {
    rows.push({ label: 'LP lock until', value: isDate(r.lp_lock_until) ? fmtDateTime(r.lp_lock_until) : r.lp_lock_until });
  }
  if (r.min_liquidity_usd !== null) rows.push({ label: 'Min liquidity', value: fmtUsd(r.min_liquidity_usd) });
  if (r.hook_address !== null) rows.push({ label: 'Hook contract', value: <Addr address={r.hook_address} /> });
  if (r.lp_lock_address !== null) rows.push({ label: 'LP lock contract', value: <Addr address={r.lp_lock_address} /> });
  return rows;
}

export const HookRulesBlock: React.FC<{ rules: Rules; pending: boolean }> = ({ rules, pending }) => {
  const rows = pending ? [] : ruleRows(rules);
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)] mb-1.5">Hook rules</div>
      {rows.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--dim)]">{RULES_PENDING}</p>
      ) : (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 font-mono text-[12px]">
          {rows.map((r) => (
            <React.Fragment key={r.label}>
              <dt className="text-[var(--dim)]">{r.label}</dt>
              <dd className="text-[var(--fg)] min-w-0">{r.value}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
    </div>
  );
};
