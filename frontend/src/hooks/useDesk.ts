import { useEffect } from 'react';
import { useEmileStore } from '@/store/useEmileStore';
import { getApiBaseUrl } from '@/config/constants';
import type { DeskPayload } from '@/components/desk/types';

// Fallback poll in case the WebSocket drops. The backend caches /api/desk for 5s and the worker ticks every 60s.
const POLL_MS = 30_000;
// After a WS event the row is patched in place; refetch once the backend cache has expired for the totals
const AFTER_EVENT_MS = 6_000;

export function useDesk() {
  const setDesk = useEmileStore((s) => s.setDesk);
  const version = useEmileStore((s) => s.deskVersion);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/api/desk`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: DeskPayload = await res.json();
        if (!cancelled) setDesk(data);
      } catch (err) {
        console.warn('Desk data unavailable:', err);
        if (!cancelled) setDesk(null, true);
      }
    };
    const first = setTimeout(load, version === 0 ? 0 : AFTER_EVENT_MS);
    const t = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(first);
      clearInterval(t);
    };
  }, [setDesk, version]);
}

/** One trade with its full Why card. */
export async function fetchDeskTrade(id: string) {
  const res = await fetch(`${getApiBaseUrl()}/api/desk/trades/${encodeURIComponent(id)}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
