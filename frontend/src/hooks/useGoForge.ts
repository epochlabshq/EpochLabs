import { useCallback, useEffect } from 'react';
import { useEmileStore } from '@/store/useEmileStore';
import { gfApi } from '@/lib/gfApi';

// Fallback polls in case the WebSocket drops. Live changes stream through /stream (gf_vote, gf_phase, ...).
const ROUND_POLL_MS = 60_000;
const LAUNCHES_POLL_MS = 180_000;
// After a WS event the backend caches the round for up to 5 s: read again just after
const AFTER_EVENT_MS = 6_000;

/** Loads the round, the archive and the viewer into the store, and keeps them fresh. Mount once per page. */
export function useGoForge(enabled = true) {
  const { setGfRound, setGfLaunches, setGfMe } = useEmileStore.getState();
  const version = useEmileStore((s) => s.gfVersion);

  const refreshMe = useCallback(async () => {
    try {
      useEmileStore.getState().setGfMe(await gfApi.me());
    } catch {
      /* a failed read keeps the last known viewer */
    }
  }, []);

  useEffect(() => {
    if (!enabled) return; // coming-soon mode: no requests
    let cancelled = false;
    const loadRound = async () => {
      try {
        const data = await gfApi.round();
        if (!cancelled) setGfRound(data);
      } catch (err) {
        console.warn('GoForge round unavailable:', err);
        if (!cancelled) setGfRound(null, true);
      }
    };
    const first = setTimeout(loadRound, version === 0 ? 0 : AFTER_EVENT_MS);
    const t = setInterval(loadRound, ROUND_POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(first);
      clearInterval(t);
    };
  }, [enabled, setGfRound, version]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await gfApi.launches();
        if (!cancelled) setGfLaunches(data);
      } catch (err) {
        console.warn('GoForge launches unavailable:', err);
      }
    };
    const first = setTimeout(load, version === 0 ? 0 : AFTER_EVENT_MS);
    const t = setInterval(load, LAUNCHES_POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(first);
      clearInterval(t);
    };
  }, [enabled, setGfLaunches, version]);

  useEffect(() => {
    if (!enabled) return;
    void refreshMe();
    // the viewer changes after X login (redirect back) and after phase events
  }, [enabled, refreshMe, version]);

  return { refreshMe, setGfMe };
}

/** MC snapshots of one launch's first 48 hours (404 for a launch that is not public). */
export async function fetchGoForgeHistory(id: string) {
  const res = await fetch(`/api/goforge/${encodeURIComponent(id)}/history`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
