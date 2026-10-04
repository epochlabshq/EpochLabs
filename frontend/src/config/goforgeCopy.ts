// Copy for /goforge, verbatim from the Developer Brief. Text only: statuses and numbers come from /api/goforge.

export const GOFORGE_HERO = {
  title: 'GoForge.',
  subtitle: 'What Golem builds from what it learned.',
  intro:
    'Every token Golem launches on its own, explained win or lose. The record is built from the chain, Blockscout and DexScreener, and the 48 hour verdict cannot be edited after the fact.',
};

export const GATE_LABEL: Record<string, string> = {
  locked: 'Locked',
  ready: 'Ready',
  forging: 'Forging',
  cooldown: 'Cooldown',
};

export const GATE_COPY: Record<string, string> = {
  locked: 'Golem does not launch until every gate below holds.',
  ready: 'Every gate holds. Golem can launch.',
  forging: 'A launch is inside its 48 hour window.',
  cooldown: 'Every other gate holds. Golem waits out the cooldown.',
};

export const GOFORGE_EMPTY = 'No launches yet. GoForge opens when every gate holds.';

export const VERDICT_LABEL: Record<string, string> = {
  pending: 'Pending',
  reached_30k: 'Reached $30K',
  stalled: 'Stalled',
};

export const RULES_PENDING = 'Rules: pending deployment.';

export const GOFORGE_DISCLAIMER =
  'Golem launches tokens as a research experiment. Not financial advice. Golem never buys or sells its own launches.';

export const GOFORGE_ADDRESSES = {
  launcher: '0x75fd64Cc8D57c529f34089Ac9083E704c23F0D8B',
  agent: '0x560Eb3767434006b3278810f906d7677C38914aD',
  wallet: '0x49EdF5f24216e02EEb6a947cC3dF0CDB6B84582C',
};

export const BLOCKSCOUT_BASE = 'https://robinhoodchain.blockscout.com';

// GoForge ships dark: the page shows "Coming soon" until NEXT_PUBLIC_GOFORGE_LIVE=true (set it when the first launch is near).
export const GOFORGE_LIVE = process.env.NEXT_PUBLIC_GOFORGE_LIVE === 'true';

export const GOFORGE_SOON = {
  badge: 'Coming soon',
  title: 'GoForge opens when every gate holds.',
  body: 'Golem has not launched a token of its own yet. When it does, every launch will appear here with live numbers, a locked 48 hour verdict and the reason it was launched, win or lose.',
  items: [
    { title: 'Launch Gate', text: 'Five conditions Golem must meet before it launches, shown live.' },
    { title: '48 hour verdict', text: 'Reached $30K or stalled. Locked at hour 48 and never edited.' },
    { title: 'Why Golem launched it', text: 'The logged reason and its hash, so it cannot be rewritten after the result.' },
  ],
};
