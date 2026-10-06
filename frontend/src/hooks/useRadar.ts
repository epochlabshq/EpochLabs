import { useEffect, useState } from 'react';
import { getApiBaseUrl } from '@/config/constants';
import type { RadarDetail, RadarExamples, RadarPayload, RadarPointsPayload } from '@/components/radar/types';

export type RadarState = 'loading' | 'ready' | 'empty' | 'error';

interface RadarData {
  state: RadarState;
  data: RadarPayload | null;
  points: RadarPointsPayload | null;
}

// Dev aid: NEXT_PUBLIC_RADAR_API_BASE_URL points only the radar at another API (e.g. the demo server)
const radarBase = () => (process.env.NEXT_PUBLIC_RADAR_API_BASE_URL || getApiBaseUrl()).replace(/\/+$/, '');

async function getJson<T>(path: string, signal?: AbortSignal): Promise<{ status: number; body: T | null }> {
  const res = await fetch(`${radarBase()}${path}`, { signal });
  if (res.status === 404) return { status: 404, body: null };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return { status: res.status, body: (await res.json()) as T };
}

/** The radar is rebuilt once a day and cached for an hour server side: one fetch on mount is enough. */
export function useRadar(): RadarData {
  const [state, setState] = useState<RadarData>({ state: 'loading', data: null, points: null });

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      try {
        const [radar, points] = await Promise.all([
          getJson<RadarPayload>('/api/radar', ctrl.signal),
          getJson<RadarPointsPayload>('/api/radar/points', ctrl.signal),
        ]);
        if (radar.status === 404 || !radar.body) {
          setState({ state: 'empty', data: null, points: null });
          return;
        }
        setState({ state: 'ready', data: radar.body, points: points.body });
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        console.warn('Radar data unavailable:', err);
        setState({ state: 'error', data: null, points: null });
      }
    })();
    return () => ctrl.abort();
  }, []);

  return state;
}

export async function fetchRadarDetail(id: string, signal?: AbortSignal): Promise<RadarDetail> {
  const r = await getJson<RadarDetail>(`/api/radar/${encodeURIComponent(id)}`, signal);
  if (!r.body) throw new Error('not found');
  return r.body;
}

export async function fetchRadarExamples(id: string, signal?: AbortSignal): Promise<RadarExamples> {
  const r = await getJson<RadarExamples>(`/api/radar/${encodeURIComponent(id)}/examples`, signal);
  if (!r.body) throw new Error('not found');
  return r.body;
}
