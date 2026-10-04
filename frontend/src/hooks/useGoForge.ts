import { useEffect } from 'react';
import { useEmileStore } from '@/store/useEmileStore';
import { getApiBaseUrl } from '@/config/constants';
import type { GoForgeHistory, GoForgePayload } from '@/components/goforge/types';

// Fallback poll in case the WebSocket drops. Live updates stream via WebSocket /stream.
const POLL_MS = 60_000;
// After a WS event the card is patched in place; refetch once the backend cache (15s) has expired
const AFTER_EVENT_MS = 16_000;

export function useGoForge(enabled = true) {
  const setGoForge = useEmileStore((s) => s.setGoForge);
  const version = useEmileStore((s) => s.goforgeVersion);

  useEffect(() => {
    if (!enabled) return; // coming-soon mode: no requests
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/api/goforge`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: GoForgePayload = await res.json();
        if (!cancelled) setGoForge(data);
      } catch (err) {
        console.warn('GoForge data unavailable:', err);
        if (!cancelled) setGoForge(null, true);
      }
    };
    const first = setTimeout(load, version === 0 ? 0 : AFTER_EVENT_MS);
    const t = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(first);
      clearInterval(t);
    };
  }, [enabled, setGoForge, version]);
}

/** MC snapshots of one launch's first 48 hours (404 for a launch that is not public). */
export async function fetchGoForgeHistory(id: string): Promise<GoForgeHistory> {
  const res = await fetch(`${getApiBaseUrl()}/api/goforge/${encodeURIComponent(id)}/history`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
