// Copy for /goforge. Text only: every number (fees, thresholds, slots) comes from /api/goforge/round.

// GoForge ships dark: the page shows "Coming soon" until NEXT_PUBLIC_GOFORGE_LIVE=true (set it once the backend has its
// login, X app and admin keys, see backend/.env.example).
export const GOFORGE_LIVE = process.env.NEXT_PUBLIC_GOFORGE_LIVE === 'true';

export const GOFORGE_HERO = {
  title: 'GoForge.',
  subtitle: 'You submit. The community votes. Golem forges one a day.',
  intro:
    'Anyone can submit a token idea. EPC holders vote for their favourite. Every day Golem scores the pool, picks one winner and launches it on Pons. Half of the creator fees goes to the creator, half buys back and burns EPC.',
};

export const GOFORGE_SOON = {
  badge: 'Coming soon',
  title: 'GoForge opens soon.',
  body: 'A new token idea is forged every day. Submit yours, let EPC holders vote, and Golem launches the winner. The creator earns half of the fees, the other half buys back and burns EPC.',
  items: [
    { title: 'Submit', text: 'Name, ticker, lore and a square image, signed with your wallet and your X account.' },
    { title: 'Vote', text: 'EPC holders vote with a signature, no gas. One wallet, one vote a day.' },
    { title: 'Golem forges one', text: 'Scored on creator credibility, Golem’s own model and the vote. Every score is published.' },
  ],
};

export const VERDICT_LABEL: Record<string, string> = {
  pending: 'Pending',
  reached_30k: 'Reached $30K',
  stalled: 'Stalled',
};

export const GOFORGE_DISCLAIMER =
  'GoForge launches community ideas as a research experiment. Epoch Labs and Golem hold no supply and never trade GoForge tokens. Not financial advice.';

export const TEAM_RULE = 'The Epoch Labs team and Golem’s own wallets cannot submit or vote.';

export const BLOCKSCOUT_BASE = 'https://robinhoodchain.blockscout.com';

/** The moderation rules. The fee line only exists when there is a fee, and review is skipped on a dev-open backend. */
export const moderationRules = (opts: { paid: boolean; manualReview: boolean }) => [
  opts.manualReview
    ? 'Nothing reaches the pool without a person on the team approving it.'
    : 'Ideas that pass the automatic checks join the pool right away.',
  'No brand, company or real person’s name (for example NVIDIA or ELON), in the name or the ticker.',
  'The ticker cannot collide with a token already on Robinhood Chain or a large listed stock.',
  'The image cannot show a well known logo, and must pass the NSFW check.',
  'A near copy of another idea submitted the same day is rejected.',
  ...(opts.paid ? ['The submit fee is not refunded, even when an idea is rejected.'] : []),
];
