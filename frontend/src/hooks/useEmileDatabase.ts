import { useEffect } from 'react';
import { useEmileStore, TokenItem } from '@/store/useEmileStore';
import { getApiBaseUrl, getWsBaseUrl } from '@/config/constants';

export function useEmileDatabase() {
  const addToken = useEmileStore((state) => state.addToken);
  const updateModel = useEmileStore((state) => state.updateModel);
  const setSimParams = useEmileStore((state) => state.setSimParams);
  const setConnected = useEmileStore((state) => state.setConnected);
  const setThresholds = useEmileStore((state) => state.setThresholds);
  const onEpochEvent = useEmileStore((state) => state.onEpochEvent);
  const requestEpochsRefresh = useEmileStore((state) => state.requestEpochsRefresh);
  const onDeskEvent = useEmileStore((state) => state.onDeskEvent);
  const onGoForgeEvent = useEmileStore((state) => state.onGoForgeEvent);

  useEffect(() => {
    const apiBase = getApiBaseUrl();
    const wsBase = getWsBaseUrl();

    // 1. Fetch initial snapshot state from PostgreSQL DB via REST API
    async function fetchStateFromDB() {
      try {
        const res = await fetch(`${apiBase}/api/state`);
        if (res.ok) {
          const data = await res.json();
          const counters = data.counters || {};
          const latestModel = data.latest_model;
          const dbTokens = data.tokens || [];

          // Deduplicate tokens by mint address
          const uniqueMap = new Map();
          dbTokens.forEach((t: any) => {
            if (t.mint && !uniqueMap.has(t.mint)) {
              uniqueMap.set(t.mint, t);
            }
          });
          const uniqueTokens = Array.from(uniqueMap.values());

          // Update Store with real Database metrics
          const formattedTokens = uniqueTokens.map((t: any) => ({
            mint: t.mint,
            name: t.name || 'Robinhood Chain Token',
            symbol: t.symbol || (t.mint ? t.mint.slice(0, 6).toUpperCase() : 'SOL'),
            lore: t.lore || 'No lore description provided.',
            lore_withheld: t.lore_withheld || false,
            logo: t.logo,
            holders: t.holders ? t.holders : null, // 0 or null = not sampled yet (sampled at 48h)
            peak_mc: t.peak_mc || 10500,
            status: ((t.status === 'passed' || (t.peak_mc && t.peak_mc >= 30000)) ? 'passed' : t.status === 'stalled' ? 'stalled' : 'pending') as 'passed' | 'stalled' | 'pending',
            hour: t.hour ?? t.launch_hour ?? (t.launched_at ? new Date(t.launched_at).getUTCHours() : 4),
            launched_at: t.launched_at
          }));

          const totalTokensInDB = counters.above_10k ?? formattedTokens.length;

          useEmileStore.setState({
            tally: {
              all: totalTokensInDB,
              pass: counters.passed_30k ?? 0,
              stall: counters.stalled ?? (counters.above_10k ? counters.above_10k - counters.passed_30k : formattedTokens.length)
            },
            counters: {
              pump: totalTokensInDB,
              dex: totalTokensInDB,
              rpc: totalTokensInDB
            },
            holdersList: counters.median_holders ? [counters.median_holders] : [],
            tokens: formattedTokens,
            ...(latestModel ? {
              simState: {
                n: latestModel.n,
                auc: latestModel.auc,
                d: latestModel.d,
                running: false
              }
            } : {})
          });

          if (data.gates_config) {
            setThresholds({
              gatesConfig: data.gates_config,
              targetAuc: data.target_auc,
              floorAuc: data.floor_auc
            });
          }

          if (latestModel) {
            updateModel({
              run_id: latestModel.run_id,
              n: latestModel.n,
              n_positive: latestModel.n_positive,
              d: latestModel.d,
              auc: latestModel.auc,
              auc_std: latestModel.auc_std,
              epsilon_vc: latestModel.epsilon_vc,
              auc_boot_lower: latestModel.auc_boot_lower,
              proven_floor: latestModel.proven_floor,
              jar_level: latestModel.jar_level,
              gates: latestModel.gates,
              blocked_by: latestModel.blocked_by,
              hour_rates: latestModel.hour_rates,
              feature_importance: latestModel.feature_importance
            });
          }
        }
      } catch (err) {
        console.warn('Backend REST API unavailable, using cached state:', err);
      }
    }

    fetchStateFromDB();

    // 2. Connect to WebSocket stream for live events
    let socket: WebSocket | null = null;
    try {
      socket = new WebSocket(`${wsBase}/stream`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => setConnected(false);
      socket.onerror = () => setConnected(false);
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.token) {
            const raw = payload.token;
            const newItem: TokenItem = {
              mint: raw.mint,
              name: raw.name || 'Robinhood Chain Token',
              symbol: raw.symbol || (raw.mint ? raw.mint.slice(0, 6).toUpperCase() : 'SOL'),
              lore: raw.lore || 'No lore description provided.',
              holders: raw.holders ? raw.holders : null,
              peak_mc: raw.peak_mc || 10500,
              status: ((raw.status === 'passed' || (raw.peak_mc && raw.peak_mc >= 30000)) ? 'passed' : raw.status === 'stalled' ? 'stalled' : 'pending') as 'passed' | 'stalled' | 'pending',
              hour: raw.hour ?? new Date().getUTCHours(),
              launched_at: raw.launched_at || new Date().toISOString()
            };
            addToken(newItem);

            // Sync simState.n live with updated DB total
            const curState = useEmileStore.getState();
            setSimParams({ n: curState.tally.all });
          }
          if (payload.epoch) {
            onEpochEvent(payload.epoch);
          }
          if (payload.desk_state || payload.desk_waiting || payload.desk_open || payload.desk_close) {
            onDeskEvent(payload);
          }
          if (payload.goforge_update || payload.goforge_verdict || payload.goforge_burn) {
            onGoForgeEvent(payload);
          }
          if (payload.model) {
            updateModel(payload.model);
            // Epoch I/II progress is read from the same model run: refresh it too
            requestEpochsRefresh();
            setSimParams({
              n: payload.model.n,
              auc: payload.model.auc,
              d: payload.model.d
            });
          }
        } catch (e) {
          // Ignore invalid WS payloads
        }
      };
    } catch (e) {
      setConnected(false);
    }

    return () => {
      if (socket) socket.close();
    };
  }, [addToken, updateModel, setSimParams, setConnected, setThresholds, onEpochEvent, requestEpochsRefresh, onDeskEvent, onGoForgeEvent]);
}
