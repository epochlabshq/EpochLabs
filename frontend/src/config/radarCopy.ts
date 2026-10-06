// Copy for /radar. Text only: every number comes from /api/radar.
// House rules (brief section 7): say "historically", "reached", "survival rate". Never promise or recommend.

export const RADAR_HERO = {
  title: 'Meta Radar.',
  subtitle: 'Which narratives survive on Robinhood Chain.',
  intro:
    'Every token that cleared $10K, grouped by how alike their lore reads. For each narrative: the share that went on to reach $30K within 48 hours, how busy it is, and whether it looks crowded.',
};

export const RADAR_DISCLAIMER =
  'Historical survival rates by narrative. Not predictions, not signals, not financial advice.';

export const STATUS_LABEL: Record<string, string> = {
  rising: 'Rising',
  cooling: 'Cooling',
  saturated: 'Saturated',
  steady: 'Steady',
  low_data: 'Low data',
};

export const STATUS_HINT: Record<string, string> = {
  rising: 'Launches in the last 7 days are up on the 7 days before.',
  cooling: 'Launches in the last 7 days are down on the 7 days before.',
  saturated: 'Crowded right now, and recent survival is below its 30-day average.',
  steady: 'Launch pace is close to the previous week.',
  low_data: 'Too few resolved tokens to show a survival rate.',
};

export const SATURATION_WARNING =
  'This narrative is crowded right now, and recent survival is below its 30-day average.';

export const NO_DATA = (n: number) => `Not enough data yet (n = ${n})`;

export const RADAR_EMPTY = 'Radar has not produced its first run yet. It runs once a day.';
export const RADAR_ERROR = 'Radar data unavailable. Nothing is shown unless it can be read from /api/radar.';

export const MAP_LEGEND = [
  { key: 'reached', text: 'Reached $30K in 48h' },
  { key: 'stalled', text: 'Stalled' },
  { key: 'pending', text: 'Still inside 48h' },
];

export const FOR_CARDS = [
  {
    title: 'For builders',
    points: [
      'Look at which narratives historically held up, not which ones were loudest.',
      'Compare a narrative with the chain baseline (the lift column), and read the interval, not just the percentage.',
      'A crowded narrative with weaker recent survival is a reason to stand out, not to copy.',
    ],
  },
  {
    title: 'For traders',
    points: [
      'See which narratives are busy now through launches and trend over 7 days.',
      'Saturated means the share of launches jumped and recent survival slipped below its 30-day average.',
      'This is aggregate history. It says nothing about any single token, and it does not list them.',
    ],
  },
];

export const METHODOLOGY_STEPS = [
  'Every token that cleared $10K and has lore text is embedded with MiniLM (384 dimensions) and normalised.',
  'Tokens are grouped by similarity. A token that fits no group stays Unclustered, it is never forced into one.',
  'Each group is named after its top keywords (class-based TF-IDF over the lore inside it).',
  'Survival rate is the share of resolved tokens that reached $30K within 48 hours. A token still inside its 48 hours is counted as activity only.',
  'Every rate carries a 95% Wilson interval and its sample size. Under 20 resolved tokens no percentage is shown, from 20 to 49 it is marked low confidence.',
  'The run repeats once a day. Group identities are matched to the previous run so a narrative keeps its history.',
];
