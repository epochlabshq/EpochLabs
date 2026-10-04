import { create } from 'zustand';
import type { EpochEvent, EpochsPayload } from '@/components/epochs/types';
import type { ClosedTrade, DeskEvent, DeskPayload, OpenTrade } from '@/components/desk/types';
import type { GoForgeEvent, GoForgePayload, GoForgeTotals, Launch } from '@/components/goforge/types';

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

  // GoForge (/api/goforge). WS goforge_* events patch it in place; `goforgeVersion` bumps to trigger a refetch.
  goforge: GoForgePayload | null;
  goforgeError: boolean;
  goforgeVersion: number;
  goforgeFlashId: string | null;

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
  setGoForge: (payload: GoForgePayload | null, error?: boolean) => void;
  onGoForgeEvent: (evt: GoForgeEvent) => void;
  setSimParams: (params: Partial<{ n: number; auc: number; d: number; running: boolean }>) => void;
  resetSim: () => void;
}

const HUES = [38, 152, 268, 196, 12, 88, 320];

const launchedAtMs = (l: Launch) => (l.launched_at ? new Date(l.launched_at).getTime() : 0);

/** Counters from the launch list. Fees and burn are only known server-side: keep the last totals for those. */
function recount(launches: Launch[], prev: GoForgeTotals): GoForgeTotals {
  return {
    ...prev,
    launches: launches.length,
    reached_30k: launches.filter((l) => l.verdict === 'reached_30k').length,
    stalled: launches.filter((l) => l.verdict === 'stalled').length,
    pending: launches.filter((l) => l.verdict === 'pending').length,
  };
}

function upsertLaunch(g: GoForgePayload, launch: Launch): GoForgePayload {
  const launches = [launch, ...g.launches.filter((l) => l.id !== launch.id)].sort((a, b) => launchedAtMs(b) - launchedAtMs(a));
  return { ...g, launches, totals: recount(launches, g.totals) };
}

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

  goforge: null,
  goforgeError: false,
  goforgeVersion: 0,
  goforgeFlashId: null,

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

  setGoForge: (payload, error = false) => set(payload ? { goforge: payload, goforgeError: false } : { goforgeError: error }),

  onGoForgeEvent: (evt) => set((state) => {
    const bump = { goforgeVersion: state.goforgeVersion + 1 };
    const g = state.goforge;
    if (!g) return bump;
    if ('goforge_update' in evt) {
      return { ...bump, goforgeFlashId: evt.goforge_update.id, goforge: upsertLaunch(g, evt.goforge_update) };
    }
    if ('goforge_verdict' in evt) {
      return { ...bump, goforgeFlashId: evt.goforge_verdict.id, goforge: upsertLaunch(g, evt.goforge_verdict.launch) };
    }
    const { id, epc_burned } = evt.goforge_burn;
    const launches = g.launches.map((l) => (l.id === id ? { ...l, epc_burned } : l));
    return { ...bump, goforgeFlashId: id, goforge: { ...g, launches } };
  }),

  setSimParams: (params) => set((state) => ({ simState: { ...state.simState, ...params } })),

  resetSim: () => set((state) => ({
    simState: { n: 340, auc: 0.548, d: state.model.d || state.simState.d, running: true }
  }))
}));
