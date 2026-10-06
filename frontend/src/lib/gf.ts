// Pure helpers for the GoForge registry page. No React, no browser APIs: they are unit-tested with node --test
// (frontend/src/lib/gf.test.ts) and imported by the wallet layer and the components.

export const ERC20_TRANSFER_SELECTOR = '0xa9059cbb';
export const IMAGE_MAX_BYTES = 2 * 1024 * 1024;
export const IMAGE_MIN_SIDE = 512;
export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

export const NAME_RANGE = [2, 32] as const;
export const TICKER_RANGE = [2, 10] as const;
export const LORE_RANGE = [50, 1000] as const;

// ---- SIWE (EIP-4361): the same text the backend parses, so a signature made here verifies there ----

export interface SiweFields {
  domain: string;
  address: string;
  uri: string;
  chainId: number;
  nonce: string;
  issuedAt: string; // ISO, from the server
  statement: string;
}

export function buildSiweMessage(f: SiweFields): string {
  return [
    `${f.domain} wants you to sign in with your Ethereum account:`,
    f.address,
    '',
    f.statement,
    '',
    `URI: ${f.uri}`,
    'Version: 1',
    `Chain ID: ${f.chainId}`,
    `Nonce: ${f.nonce}`,
    `Issued At: ${f.issuedAt}`,
  ].join('\n');
}

// ---- Payment of the submit fee: an EPC transfer to the burn address ----

const pad32 = (hex: string) => hex.replace(/^0x/, '').toLowerCase().padStart(64, '0');

/** Whole EPC (a decimal number) to wei as a bigint, without float error. */
export function epcToWei(amount: number, decimals = 18): bigint {
  const [whole, frac = ''] = String(amount).split('.');
  const fracPadded = (frac + '0'.repeat(decimals)).slice(0, decimals);
  return BigInt(whole) * BigInt('1' + '0'.repeat(decimals)) + BigInt(fracPadded || '0');
}

/** transfer(address,uint256) calldata. */
export function erc20TransferData(to: string, amountWei: bigint): string {
  if (!/^0x[0-9a-fA-F]{40}$/.test(to)) throw new Error('Invalid recipient address');
  return ERC20_TRANSFER_SELECTOR + pad32(to) + pad32(amountWei.toString(16));
}

export const isTxHash = (s: string) => /^0x[0-9a-fA-F]{64}$/.test(s.trim());

// ---- Form validation (the backend checks the same rules again) ----

export interface FieldErrors {
  name?: string;
  ticker?: string;
  lore?: string;
  image?: string;
  fee_tx?: string;
}

export function normalizeTicker(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, TICKER_RANGE[1]);
}

export function validateIdea(f: { name: string; ticker: string; lore: string }): FieldErrors {
  const e: FieldErrors = {};
  const name = f.name.trim();
  if (name.length < NAME_RANGE[0] || name.length > NAME_RANGE[1]) e.name = `Name must be ${NAME_RANGE[0]}-${NAME_RANGE[1]} characters.`;
  if (f.ticker.length < TICKER_RANGE[0] || f.ticker.length > TICKER_RANGE[1]) e.ticker = `Ticker must be ${TICKER_RANGE[0]}-${TICKER_RANGE[1]} letters or numbers.`;
  else if (!/^[A-Z0-9]+$/.test(f.ticker)) e.ticker = 'Ticker can only use letters and numbers.';
  const lore = f.lore.trim();
  if (lore.length < LORE_RANGE[0] || lore.length > LORE_RANGE[1]) e.lore = `Lore must be ${LORE_RANGE[0]}-${LORE_RANGE[1].toLocaleString('en-US')} characters.`;
  return e;
}

export function validateImageMeta(m: { type: string; size: number; width: number; height: number }): string | null {
  if (!IMAGE_TYPES.includes(m.type)) return 'Image must be PNG, JPG or WebP.';
  if (m.size > IMAGE_MAX_BYTES) return 'Image must be 2 MB or smaller.';
  if (m.width !== m.height) return `Image must be square (1:1). This one is ${m.width}x${m.height}.`;
  if (m.width < IMAGE_MIN_SIDE) return `Image must be at least ${IMAGE_MIN_SIDE}x${IMAGE_MIN_SIDE}px.`;
  return null;
}

// ---- Round countdown and labels ----

export const NEXT_LABEL: Record<string, string> = {
  submit_closes: 'Submit closes in',
  vote_closes: 'Voting closes in',
  announcement: 'Winner announced in',
  submit_opens: 'Next round opens in',
};

export const PHASE_LABEL: Record<string, string> = {
  submit: 'Submit open',
  vote: 'Voting open',
  scoring: 'Scoring',
  announced: 'Winner announced',
};

export const NO_LAUNCH_TEXT: Record<string, string> = {
  no_approved_ideas: 'No idea passed moderation today.',
  no_votes: 'No eligible vote was cast today.',
  all_creators_in_cooldown: 'Every ranked creator had already won within the last 7 days.',
};

export const secondsUntil = (iso: string, nowMs: number) => Math.max(0, Math.floor((new Date(iso).getTime() - nowMs) / 1000));

// ---- Server error codes -> sentences the user can act on ----

const ERROR_TEXT: Record<string, string> = {
  login_required: 'Connect your wallet first.',
  login_not_configured: 'Login is not available yet.',
  x_not_linked: 'Connect your X account before submitting.',
  submissions_closed: 'Submissions are closed right now.',
  slots_full: 'All slots for today are taken.',
  already_submitted_today: 'This wallet already submitted an idea today.',
  fee_tx_used: 'That fee transaction was already used for another idea.',
  fee_tx_invalid: 'The fee transaction hash is not valid.',
  fee_tx_rejected: 'The fee transaction could not be accepted.',
  chain_unavailable: 'The chain could not be read right now. Try again in a minute.',
  team_not_allowed: 'The Epoch Labs team cannot take part.',
  voting_closed: 'Voting is not open right now.',
  wrong_round: 'That vote is for a different round.',
  idea_not_votable: 'That idea is not in today’s pool.',
  self_vote: 'You cannot vote for your own idea.',
  stale_signature: 'That signature is older than your current vote. Sign again.',
  vote_rate_limited: 'Too many vote changes this hour.',
  bad_signature: 'The signature was not accepted.',
};

export function errorText(detail: { code?: string; message?: string } | undefined | null, fallback = 'Something went wrong. Try again.'): string {
  if (!detail) return fallback;
  return detail.message || (detail.code && ERROR_TEXT[detail.code]) || fallback;
}

/** Wallet errors: EIP-1193 code 4001 is the user closing the prompt, which is not a failure to report. */
export function walletErrorText(err: unknown): string | null {
  type E = { code?: number; message?: string; shortMessage?: string; cause?: unknown };
  const e = err as E | undefined;
  // viem and wagmi wrap the wallet's error: the EIP-1193 code can sit a few causes down
  for (let c: E | undefined = e, depth = 0; c && depth < 5; c = c.cause as E | undefined, depth++) {
    if (c.code === 4001) return null;
    if (c.code === -32002) return 'A request is already open in your wallet.';
  }
  const text = e?.shortMessage || e?.message;
  return text ? String(text).slice(0, 160) : 'The wallet request failed.';
}

export const xProfileUrl = (handle: string | null | undefined) => (handle ? `https://x.com/${handle.replace(/^@/, '')}` : null);
