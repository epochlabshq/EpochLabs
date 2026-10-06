import { create } from 'zustand';
import type { EpochEvent, EpochsPayload } from '@/components/epochs/types';
import type { ClosedTrade, DeskEvent, DeskPayload, OpenTrade } from '@/components/desk/types';
import type { GfEvent, LaunchesPayload, Me, RoundPayload } from '@/components/goforge/types';

export interface TokenItem {
  mint: string;
  name: string;
  symbol: string;
  lore: string | null;
  lore_withheld?: boolean;
  logo?: string;
  holders: number | null; // null until sampled at the 48h label
  peak_mc: number;
  status: 'passed' | 'stalled' | 'pending';
  hour: number;
  launched_at?: string;
  hue?: number;
}

export interface GatesConfig {
  n_samples_min: number;
  n_positive_min: number;
  auc_std_max: number;
  time_split_gap_max: number;
}

export interface ModelMetrics {
  run_id?: number;
  n: number;
  n_positive: number;
  d: number;
  auc: number;
  auc_std: number;
  epsilon_vc: number;
  auc_boot_lower: number;
  proven_floor: number;
  jar_level: number;
  gates: Record<string, boolean>;
  blocked_by: string | null;
  hour_rates?: Record<string, number>;
  feature_importance?: Record<string, number>;
}

interface EmileState {
  // Connection & Data state
  isConnected: boolean;
  stage: string;
  codeBuffer: string;
  tokens: TokenItem[];
  counters: {
    pump: number;
    dex: number;
    rpc: number;
  };
  tally: {
    all: number;
    pass: number;
    stall: number;
  };
  holdersList: number[];

  // Model state (zeros until /api/state or the WS `model` event arrives)
  model: ModelMetrics;
  modelLoaded: boolean;
  // Gate thresholds and AUC bounds, served by /api/state. Never hardcode them in components.
  gatesConfig: GatesConfig | null;
  targetAuc: number | null;
  floorAuc: number | null;

  // Epochs page (/api/epochs). `epochsVersion` bumps on WS events to trigger a refetch.
  epochs: EpochsPayload | null;
  epochsError: boolean;
  epochsVersion: number;
  justCompleted: number | null;

  // The Desk (/api/desk). WS desk_* events patch it in place; `deskVersion` bumps to trigger a refetch
  // so totals and the heartbeat catch up with the event.
  desk: DeskPayload | null;
  deskError: boolean;
  deskVersion: number;
  deskFlashId: string | null;

  // GoForge registry. Round, launches and the viewer are fetched by useGoForge; WS gf_* / goforge_* events patch the vote
  // counts in place and bump `gfVersion` so everything else is read again.
  gfRound: RoundPayload | null;
  gfRoundError: boolean;
  // server clock minus this browser's clock at the last read: countdowns follow the server, not a wrong local clock
  gfClockOffsetMs: number;
  gfLaunches: LaunchesPayload | null;
  gfMe: Me | null;
  gfVersion: number;

  // Interactive Simulation state
  simState: {
    n: number;
    auc: number;
    d: number;
    running: boolean;
  };

  // Actions
  setConnected: (connected: boolean) => void;
  setStage: (stage: string) => void;
  setCodeBuffer: (code: string) => void;
  addToken: (token: TokenItem) => void;
  updateModel: (model: Partial<ModelMetrics>) => void;
  setThresholds: (t: { gatesConfig: GatesConfig; targetAuc: number; floorAuc: number }) => void;
  setEpochs: (payload: EpochsPayload | null, error?: boolean) => void;
  onEpochEvent: (evt: EpochEvent) => void;
  requestEpochsRefresh: () => void;
  setDesk: (payload: DeskPayload | null, error?: boolean) => void;
  onDeskEvent: (evt: DeskEvent) => void;
  setGfRound: (payload: RoundPayload | null, error?: boolean) => void;
  setGfLaunches: (payload: LaunchesPayload | null) => void;
  setGfMe: (me: Me | null) => void;
  onGfEvent: (evt: GfEvent) => void;
  bumpGf: () => void;
  setSimParams: (params: Partial<{ n: number; auc: number; d: number; running: boolean }>) => void;
  resetSim: () => void;
}

const HUES = [38, 152, 268, 196, 12, 88, 320];

export const useEmileStore = create<EmileState>((set, get) => ({
  isConnected: true,
  stage: 'ingest · robinhood chain',
  codeBuffer: '',
  tokens: [],
  counters: { pump: 0, dex: 0, rpc: 0 },
  tally: { all: 0, pass: 0, stall: 0 },
  holdersList: [],

  model: {
    n: 0,
    n_positive: 0,
    d: 0,
    auc: 0,
    auc_std: 0,
    epsilon_vc: 0,
    auc_boot_lower: 0,
    proven_floor: 0,
    jar_level: 0,
    gates: {},
    blocked_by: null
  },
  modelLoaded: false,
  gatesConfig: null,
  targetAuc: null,
  floorAuc: null,

  epochs: null,
  epochsError: false,
  epochsVersion: 0,
  justCompleted: null,

  desk: null,
  deskError: false,
  deskVersion: 0,
  deskFlashId: null,

  gfRound: null,
  gfRoundError: false,
  gfClockOffsetMs: 0,
  gfLaunches: null,
  gfMe: null,
  gfVersion: 0,

  simState: {
    n: 0,
    auc: 0.5,
    d: 0,
    running: false
  },

  setConnected: (connected) => set({ isConnected: connected }),
  setStage: (stage) => set({ stage }),
  setCodeBuffer: (codeBuffer) => set({ codeBuffer }),

  addToken: (token) => {
    // Drop queued events if tab is in background or document hidden
    if (typeof document !== 'undefined' && document.hidden) {
      return;
    }

    const hue = token.hue ?? HUES[Math.floor(Math.random() * HUES.length)];
    const item = { ...token, hue };

    set((state) => {
      const isExisting = state.tokens.some(t => t.mint === item.mint);
      const filtered = state.tokens.filter(t => t.mint !== item.mint);
      const newTokens = [item, ...filtered].slice(0, 50); // Cap DOM at 50 rows
      const isPassed = item.status === 'passed';
      const isStalled = item.status === 'stalled';

      const newTally = isExisting ? state.tally : {
        all: state.tally.all + 1,
        pass: state.tally.pass + (isPassed ? 1 : 0),
        stall: state.tally.stall + (isStalled ? 1 : 0)
      };

      const newHolders = item.holders == null ? state.holdersList : [...state.holdersList, item.holders].slice(-4000);
      const newCounters = isExisting ? state.counters : {
        pump: state.counters.pump + 1,
        dex: state.counters.dex + 1,
        rpc: state.counters.rpc + 1
      };

      return {
        tokens: newTokens,
        tally: newTally,
        holdersList: newHolders,
        counters: newCounters
      };
    });
  },

  updateModel: (metrics) => set((state) => ({ model: { ...state.model, ...metrics }, modelLoaded: true })),

  setThresholds: ({ gatesConfig, targetAuc, floorAuc }) => set({ gatesConfig, targetAuc, floorAuc }),

  setEpochs: (payload, error = false) => set(payload ? { epochs: payload, epochsError: false } : { epochsError: error }),

  // The event only marks which card to animate; the new statuses come from a fresh /api/epochs read.
  onEpochEvent: (evt) => set((state) => ({ justCompleted: evt.id, epochsVersion: state.epochsVersion + 1 })),

  requestEpochsRefresh: () => set((state) => ({ epochsVersion: state.epochsVersion + 1 })),

  setDesk: (payload, error = false) => set(payload ? { desk: payload, deskError: false } : { deskError: error }),

  onDeskEvent: (evt) => set((state) => {
    const bump = { deskVersion: state.deskVersion + 1 };
    const desk = state.desk;
    if (!desk) return bump;
    if ('desk_state' in evt) {
      return { ...bump, desk: { ...desk, state: evt.desk_state.state, blocked_by: evt.desk_state.blocked_by } };
    }
    if ('desk_waiting' in evt) {
      return { ...bump, desk: { ...desk, waiting: evt.desk_waiting.waiting } };
    }
    if ('desk_open' in evt) {
      const { status, ...trade } = evt.desk_open;
      if (status !== 'open' || desk.open.some((t) => t.id === trade.id)) return bump;
      return { ...bump, deskFlashId: trade.id, desk: { ...desk, open: [trade as OpenTrade, ...desk.open] } };
    }
    const { status, ...trade } = evt.desk_close;
    if (status !== 'closed') return bump;
    return {
      ...bump,
      deskFlashId: trade.id,
      desk: {
        ...desk,
        open: desk.open.filter((t) => t.id !== trade.id),
        closed: [trade as ClosedTrade, ...desk.closed.filter((t) => t.id !== trade.id)],
        closed_total: desk.closed.some((t) => t.id === trade.id) ? desk.closed_total : desk.closed_total + 1,
      },
    };
  }),

  setGfRound: (payload, error = false) => set(payload
    ? { gfRound: payload, gfRoundError: false, gfClockOffsetMs: Date.parse(payload.now) - Date.now() }
    : { gfRoundError: error }),
  setGfLaunches: (payload) => set({ gfLaunches: payload }),
  setGfMe: (me) => set({ gfMe: me }),
  bumpGf: () => set((state) => ({ gfVersion: state.gfVersion + 1 })),

  onGfEvent: (evt) => set((state) => {
    const bump = { gfVersion: state.gfVersion + 1 };
    // A vote only changes counts: patch them at once, the next read confirms them
    if ('gf_vote' in evt && state.gfRound && evt.gf_vote.round_date === state.gfRound.round_date) {
      const votes = evt.gf_vote.votes;
      const ideas = state.gfRound.ideas.map((i) => ({ ...i, votes: votes[i.idea_id] ?? 0 }));
      return { gfRound: { ...state.gfRound, ideas, n_votes: Object.values(votes).reduce((a, b) => a + b, 0) } };
    }
    return bump;
  }),

  setSimParams: (params) => set((state) => ({ simState: { ...state.simState, ...params } })),

  resetSim: () => set((state) => ({
    simState: { n: 340, auc: 0.548, d: state.model.d || state.simState.d, running: true }
  }))
}));
