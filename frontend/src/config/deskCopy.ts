// Copy for /desk, verbatim from the Developer Brief. Text only: states and numbers come from /api/desk.

export const DESK_HERO = {
  title: 'The Desk.',
  subtitle: 'Watch Golem work.',
  intro: 'Every token Golem watches, every entry it makes, and why. Live from its public wallet.',
};

export const DESK_STATE_COPY: Record<string, string> = {
  gated: "The hourglass isn't full yet. Golem is watching, not trading.",
  watching: 'Golem is scoring new tokens.',
  waiting: 'Golem found a candidate. Waiting for entry conditions.',
  entering: 'Golem found a candidate. Entry transaction sent, waiting for confirmation.',
  in_position: 'Golem is in a position.',
  paused: 'A gate failed after unlock. Golem has stopped opening new positions.',
};

export const DESK_STATE_LABEL: Record<string, string> = {
  gated: 'Gated',
  watching: 'Watching',
  waiting: 'Waiting',
  entering: 'Entering',
  in_position: 'In position',
  paused: 'Paused',
};

// What each blocking gate means, so "Blocked by" reads as a sentence and not a column name
export const BLOCKER_LABEL: Record<string, string> = {
  n_samples: 'not enough labeled tokens yet',
  n_positive: 'not enough tokens that reached $30K yet',
  auc_std: 'model scores vary too much across folds',
  time_split: 'model does not hold up on newer tokens yet',
  proven_floor: 'proven floor is below 0.60',
  epoch_ii_pending: 'Epoch II completion is being recorded',
  no_model_run: 'no model run yet',
};

export const DESK_COPY = {
  waitingAnon: "Token revealed after entry confirms, to protect Golem's fills.",
  closedEmpty: 'No trades yet. The first one will be posted here and on X.',
  openEmpty: 'No open positions.',
  waitingEmpty: 'No candidates in the queue.',
  footer: "Every number here comes from the chain or Golem's decision log. Research experiment. Not financial advice.",
};

// Hero card: Golem's simulated live trading (paper trades at real prices; no funds)
export const DESK_SIMULATION = {
  badge: 'Simulation · live',
  title: 'Live trading simulation',
  body: 'Golem trades on paper at real DexScreener prices, with the same entry and exit rules as live. No funds, no transactions.',
};
