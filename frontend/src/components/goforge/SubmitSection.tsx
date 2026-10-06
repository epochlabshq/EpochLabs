'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useEmileStore } from '@/store/useEmileStore';
import { useNow } from '@/components/desk/DeskUi';
import type { Busy, Errors, SubmitResult } from '@/hooks/useGfActions';
import {
  isTxHash, LORE_RANGE, NAME_RANGE, normalizeTicker, secondsUntil, validateIdea, validateImageMeta, type FieldErrors,
} from '@/lib/gf';
import { gfApi } from '@/lib/gfApi';
import { fmtCountdown, shortAddr, type OwnIdea, type RoundPayload } from './types';

interface Props {
  round: RoundPayload | null;
  busy: Busy;
  actionErrors: Errors;
  connected: boolean;
  address?: string;
  onLogin: () => void;
  onLogout: () => void;
  onPayFee: () => Promise<string | null>;
  onSubmit: (form: FormData) => Promise<SubmitResult>;
}

const STATUS_TONE: Record<string, string> = {
  pending_review: 'text-[var(--banana)] border-[var(--banana)]',
  approved: 'text-[var(--live)] border-[var(--live)]',
  rejected: 'text-[var(--stall)] border-[var(--stall)]',
};
const STATUS_LABEL: Record<string, string> = { pending_review: 'In review', approved: 'Approved', rejected: 'Rejected' };

const Step: React.FC<{ n: number; title: string; done: boolean; children: React.ReactNode }> = ({ n, title, done, children }) => (
  <li className={`rounded-xl border p-4 ${done ? 'border-[var(--live)]/50' : 'border-[var(--border)]'} bg-[var(--panel2)]`}>
    <div className="flex items-center gap-3">
      <span aria-hidden className={`w-6 h-6 rounded-full border flex items-center justify-center font-mono text-[11px] font-bold shrink-0 ${done ? 'border-[var(--live)] text-[var(--live)]' : 'border-[var(--border-strong)] text-[var(--dim)]'}`}>
        {done ? '✓' : n}
      </span>
      <h3 className="font-sans font-semibold text-[15px] text-[var(--fg-hi)]">{title}</h3>
    </div>
    <div className="mt-2 pl-9 text-[13px] text-[var(--dim)] leading-relaxed">{children}</div>
  </li>
);

const ReadOnly: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="min-w-0">
    <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{label}</div>
    <div className="mt-1 rounded-lg border border-[var(--border)] bg-[var(--panel2)] px-3 py-2 font-mono text-[12.5px] text-[var(--fg)] truncate" aria-readonly="true">{value}</div>
  </div>
);

const Field: React.FC<{ label: string; error?: string; hint?: React.ReactNode; children: React.ReactNode }> = ({ label, error, hint, children }) => (
  <label className="block min-w-0">
    <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">{label}</span>
    <div className="mt-1">{children}</div>
    {error ? <p role="alert" className="mt-1 text-[12px] text-[var(--stall)]">{error}</p> : hint ? <p className="mt-1 text-[11.5px] text-[var(--faint)]">{hint}</p> : null}
  </label>
);

const inputClass = 'w-full rounded-lg border border-[var(--border-strong)] bg-[var(--panel2)] px-3 py-2 text-[14px] text-[var(--fg-hi)] placeholder:text-[var(--faint)] focus:outline-none focus:border-[var(--banana)]';

export const SubmitSection: React.FC<Props> = ({ round, busy, actionErrors, connected, address, onLogin, onLogout, onPayFee, onSubmit }) => {
  const me = useEmileStore((s) => s.gfMe);
  // Signed in with one wallet, while the wallet app now has another account selected
  const switched = !!me?.wallet && connected && !!address && address.toLowerCase() !== me.wallet.toLowerCase();
  const now = useNow(1000) + useEmileStore((s) => s.gfClockOffsetMs);
  const [name, setName] = useState('');
  const [ticker, setTicker] = useState('');
  const [lore, setLore] = useState('');
  const [feeTx, setFeeTx] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [mine, setMine] = useState<OwnIdea[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const version = useEmileStore((s) => s.gfVersion);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  useEffect(() => {
    if (!me?.wallet) return;
    let cancelled = false;
    gfApi.myIdeas().then((r) => { if (!cancelled) setMine(r.ideas); }).catch(() => {});
    return () => { cancelled = true; };
  }, [me?.wallet, me?.submitted_today, version]);

  const open = round?.phase === 'submit' || !!round?.dev_open;
  const rules = round?.rules;
  const fee = rules?.submit_fee_epc ?? 0;
  const left = round ? secondsUntil(round.next.at, now) : 0;
  const slotsFull = !!round && round.slots.used >= round.slots.max;
  const canFill = open && !!me?.wallet && !!me.x && !me.is_team && !me.submitted_today && !slotsFull;
  const free = fee <= 0; // GF_SUBMIT_FEE_EPC=0: nothing to burn, nothing to paste
  const feeOk = free || isTxHash(feeTx);

  const onFile = (f: File | null) => {
    setImageError(null);
    setFile(null);
    setPreview(null);
    if (!f) return;
    const url = URL.createObjectURL(f);
    const img = new Image();
    img.onload = () => {
      const problem = validateImageMeta({ type: f.type, size: f.size, width: img.naturalWidth, height: img.naturalHeight });
      if (problem) {
        setImageError(problem);
        URL.revokeObjectURL(url);
        if (fileRef.current) fileRef.current.value = '';
        return;
      }
      setFile(f);
      setPreview(url);
    };
    img.onerror = () => { setImageError('The image could not be read.'); URL.revokeObjectURL(url); };
    img.src = url;
  };

  const pay = async () => {
    const hash = await onPayFee();
    if (hash) setFeeTx(hash);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const local = validateIdea({ name, ticker, lore });
    if (!file) local.image = imageError ?? 'Add a square image.';
    if (!feeOk) local.fee_tx = `Burn ${fee.toLocaleString('en-US')} EPC first, or paste the hash of your fee transaction.`;
    setErrors(local);
    if (Object.keys(local).length) return;
    const form = new FormData();
    form.set('name', name.trim());
    form.set('ticker', ticker);
    form.set('lore', lore.trim());
    form.set('fee_tx', free ? '' : feeTx.trim());
    form.set('image', file as File);
    const res = await onSubmit(form);
    if (res.idea) {
      setName(''); setTicker(''); setLore(''); setFeeTx(''); setFile(null); setPreview(null); setErrors({});
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    if (res.problems) {
      const next: FieldErrors = {};
      for (const p of res.problems) (next as Record<string, string>)[p.field] = p.message;
      setErrors(next);
    }
    if (res.error) setFormError(res.error);
  };

  const steps = useMemo(() => ({ wallet: !!me?.wallet, x: !!me?.x }), [me]);

  return (
    <section id="submit" aria-labelledby="submit-title" className="min-w-0 scroll-mt-24">
      <h2 id="submit-title" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)] mb-3">Submit your idea</h2>
      <div className="rounded-2xl border border-[var(--border-strong)] bg-[var(--panel)] p-4 md:p-6">
        {!round ? (
          <p role="status" className="font-mono text-[13px] text-[var(--dim)]">Loading…</p>
        ) : !rules?.login_configured ? (
          <p role="status" data-testid="login-off" className="text-[14px] text-[var(--dim)]">Submissions open once login is switched on. Nothing can be submitted until then.</p>
        ) : (
          <>
            {!open && (
              <div data-testid="submit-closed" className="mb-4 rounded-xl border border-dashed border-[var(--border-strong)] p-4 text-[14px] text-[var(--dim)]">
                Submissions are closed. The next round opens at {String(round.schedule.submit_opens_hour_utc).padStart(2, '0')}:00 UTC
                {round.next.label === 'submit_opens' ? <> in <span className="font-mono tabular-nums text-[var(--banana)]">{fmtCountdown(left)}</span></> : null}.
              </div>
            )}

            <ol className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <Step n={1} title="Connect your wallet" done={steps.wallet}>
                {me?.wallet && !switched ? (
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-mono text-[var(--fg)]" title={me.wallet}>{shortAddr(me.wallet)}</span>
                    <button type="button" onClick={onLogout} className="font-mono text-[11.5px] text-[var(--banana)] hover:underline">Disconnect</button>
                  </span>
                ) : (
                  <>
                    {switched && <span className="mb-2 block text-[12.5px]">Your wallet switched to {shortAddr(address as string)}. Sign in again to use it.</span>}
                    <button type="button" onClick={onLogin} disabled={busy !== null}
                      className="rounded-lg border border-[var(--banana)] bg-[var(--banana-glow)] px-4 py-2 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] text-[var(--banana)] disabled:opacity-60">
                      {busy === 'login' ? 'Waiting for your wallet…' : connected && address ? `Sign in with ${shortAddr(address)}` : 'Connect wallet'}
                    </button>
                    {actionErrors.login && <p role="alert" data-testid="login-error" className="mt-2 text-[12.5px] text-[var(--stall)]">{actionErrors.login}</p>}
                  </>
                )}
              </Step>
              <Step n={2} title="Connect your X account" done={steps.x}>
                {me?.x ? (
                  <span>
                    <span className="font-mono text-[var(--fg)]">@{me.x.handle}</span>
                    {me.x.verified ? <span className="ml-2 text-[var(--live)]">verified</span> : null}
                    <span className="block mt-1 text-[11.5px] text-[var(--faint)]">Read from X, it cannot be edited. One X account links to one wallet.</span>
                  </span>
                ) : me?.wallet ? (
                  rules.x_login_configured ? (
                    <a href={gfApi.xStartUrl} className="inline-block rounded-lg border border-[var(--banana)] bg-[var(--banana-glow)] px-4 py-2 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] text-[var(--banana)]">
                      Connect X
                    </a>
                  ) : (
                    <span>X login is not switched on yet.</span>
                  )
                ) : (
                  <span>Connect your wallet first.</span>
                )}
              </Step>
            </ol>

            {me?.is_team && <p role="status" className="mt-4 text-[13px] text-[var(--stall)]">The Epoch Labs team and Golem&apos;s own wallets cannot submit.</p>}
            {me?.submitted_today && open && <p role="status" className="mt-4 text-[13px] text-[var(--dim)]">This wallet already submitted today. One idea per wallet per day.</p>}
            {slotsFull && open && <p role="status" className="mt-4 text-[13px] text-[var(--stall)]">All {round.slots.max} slots for today are taken.</p>}

            <form onSubmit={submit} className={`mt-5 ${canFill ? '' : 'opacity-50 pointer-events-none select-none'}`} aria-disabled={!canFill} noValidate>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <ReadOnly label="Wallet" value={me?.wallet ? shortAddr(me.wallet) : 'from your signed-in wallet'} />
                <ReadOnly label="X handle" value={me?.x?.handle ? `@${me.x.handle}` : 'from your X login'} />
                <Field label="Token name" error={errors.name} hint={`${NAME_RANGE[0]}-${NAME_RANGE[1]} characters`}>
                  <input className={inputClass} value={name} maxLength={NAME_RANGE[1]} onChange={(e) => setName(e.target.value)} placeholder="Spore Keeper" disabled={!canFill} />
                </Field>
                <Field label="Ticker" error={errors.ticker} hint="Letters and numbers only, no $">
                  <input className={`${inputClass} font-mono uppercase`} value={ticker} onChange={(e) => setTicker(normalizeTicker(e.target.value))} placeholder="SPORE" disabled={!canFill} />
                </Field>
              </div>
              <div className="mt-4">
                <Field label="Lore" error={errors.lore} hint={<span className="tabular-nums">{lore.trim().length} / {LORE_RANGE[1].toLocaleString('en-US')} · at least {LORE_RANGE[0]}</span>}>
                  <textarea className={`${inputClass} min-h-[120px] resize-y`} value={lore} maxLength={LORE_RANGE[1] + 200} onChange={(e) => setLore(e.target.value)}
                    placeholder="The story of your token, in your own words. No links." disabled={!canFill} />
                </Field>
              </div>
              <div className="mt-4 grid grid-cols-1 md:grid-cols-[auto_minmax(0,1fr)] gap-4 items-start">
                <div className="w-[104px] h-[104px] rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--panel2)] overflow-hidden flex items-center justify-center text-[11px] text-[var(--faint)] text-center">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {preview ? <img src={preview} alt="Your image" className="w-full h-full object-cover" /> : '1:1'}
                </div>
                <Field label="Image" error={errors.image ?? imageError ?? undefined} hint="Square (1:1), PNG, JPG or WebP, at most 2 MB, at least 512x512. No logos or NSFW.">
                  <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" disabled={!canFill}
                    onChange={(e) => onFile(e.target.files?.[0] ?? null)}
                    className="block w-full text-[13px] text-[var(--dim)] file:mr-3 file:rounded-lg file:border file:border-[var(--border-strong)] file:bg-[var(--panel2)] file:px-3 file:py-1.5 file:font-mono file:text-[11.5px] file:text-[var(--fg-hi)]" />
                </Field>
              </div>

              {!free && (
              <div className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--panel2)] p-4">
                <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--faint)]">Submit fee</div>
                <p className="mt-1 text-[13px] text-[var(--dim)] leading-relaxed">
                  Burn <span className="font-mono text-[var(--fg-hi)]">{fee.toLocaleString('en-US')} EPC</span> to the burn address. It stops spam and counts as an EPC burn.
                  <strong className="text-[var(--stall)] font-medium"> It is not refunded, even if your idea is rejected.</strong>
                </p>
                <div className="mt-3 flex flex-wrap items-start gap-3">
                  <button type="button" onClick={pay} disabled={!canFill || busy !== null}
                    className="rounded-lg border border-[var(--banana)] bg-[var(--banana-glow)] px-4 py-2 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] text-[var(--banana)] disabled:opacity-60">
                    {busy === 'pay' ? 'Waiting for the transaction…' : `Burn ${fee.toLocaleString('en-US')} EPC`}
                  </button>
                  <div className="flex-1 min-w-[220px]">
                    <input className={`${inputClass} font-mono text-[12px]`} value={feeTx} onChange={(e) => setFeeTx(e.target.value)} placeholder="Fee transaction hash (0x…)" aria-label="Fee transaction hash" disabled={!canFill} />
                    {errors.fee_tx && <p role="alert" className="mt-1 text-[12px] text-[var(--stall)]">{errors.fee_tx}</p>}
                  </div>
                </div>
              </div>
              )}

              {(actionErrors.pay || actionErrors.submit || formError) && <p role="alert" data-testid="form-error" className="mt-4 text-[13px] text-[var(--stall)]">{formError ?? actionErrors.submit ?? actionErrors.pay}</p>}
              <button type="submit" disabled={!canFill || busy !== null}
                className="mt-5 rounded-xl bg-[var(--banana)] px-6 py-2.5 font-mono text-[12.5px] font-semibold uppercase tracking-[0.14em] text-[var(--panel)] disabled:opacity-50">
                {busy === 'submit' ? 'Submitting…' : 'Submit idea'}
              </button>
            </form>

            {mine.length > 0 && (
              <div className="mt-6">
                <h3 className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--faint)] mb-2">Your ideas</h3>
                <ul className="space-y-2">
                  {mine.map((i) => (
                    <li key={i.idea_id} data-testid="my-idea" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[var(--border)] bg-[var(--panel2)] px-3 py-2">
                      <span className="font-sans text-[14px] text-[var(--fg-hi)]">{i.name}</span>
                      <span className="font-mono text-[12px] text-[var(--banana)]">{i.ticker}</span>
                      <span className="font-mono text-[11px] text-[var(--faint)]">{i.round_date}</span>
                      <span className={`ml-auto rounded-md border px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.12em] ${STATUS_TONE[i.status]}`}>{STATUS_LABEL[i.status]}</span>
                      {i.status === 'rejected' && i.reject_reason && <p className="basis-full text-[12.5px] text-[var(--dim)]">Reason: {i.reject_reason}</p>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
};
