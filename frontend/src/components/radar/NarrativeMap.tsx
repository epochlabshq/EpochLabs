'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MAP_LEGEND, NO_DATA } from '@/config/radarCopy';
import type { Narrative, RadarPoint } from './types';
import { UNCLUSTERED_ID, clusterColor, fmtCi, fmtPct } from './types';

interface Props {
  points: RadarPoint[];
  narratives: Narrative[];
  selectedId: string | null;
  onSelect: (clusterId: string) => void;
}

const PAD = 32;
const HIT_RADIUS = 16;
const LABELLED = 6; // narratives named on the map itself, so identity never rests on colour alone

interface Placed {
  px: number;
  py: number;
  p: RadarPoint;
}

interface Hull {
  id: string;
  x: number;
  y: number;
  r: number;
}

export const NarrativeMap: React.FC<Props> = ({ points, narratives, selectedId, onSelect }) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [listHover, setListHover] = useState<string | null>(null);
  const byId = useMemo(() => new Map(narratives.map((n) => [n.cluster_id, n])), [narratives]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      setSize({ w, h: w < 640 ? 340 : 480 });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const placed = useMemo<Placed[]>(() => {
    if (!size.w || points.length === 0) return [];
    // Each axis blends linear position with rank position. Linear alone leaves a few tight groups far apart
    // in empty space (a 2D projection of many dimensions has huge gaps); rank spreads dense groups and
    // closes the gaps, while keeping which group sits near which. The map is qualitative, not to scale.
    const BLEND = 0.85;
    const axis = (vals: number[]): number[] => {
      const n = vals.length;
      const order = vals.map((v, i) => [v, i] as const).sort((a, b) => a[0] - b[0]);
      const rank = new Array<number>(n);
      order.forEach(([, i], r) => { rank[i] = n > 1 ? r / (n - 1) : 0.5; });
      const lo = order[Math.floor((n - 1) * 0.01)][0];
      const hi = order[Math.ceil((n - 1) * 0.99)][0];
      return vals.map((v, i) => {
        const lin = Math.min(Math.max((v - lo) / (hi - lo || 1), 0), 1);
        return (1 - BLEND) * lin + BLEND * rank[i];
      });
    };
    const xs = axis(points.map((p) => p.x));
    const ys = axis(points.map((p) => p.y));

    // Pull the narratives towards each other and let each one grow: every group keeps its shape but its
    // centre moves GAP of the way to the middle, then the whole map is refitted to the canvas.
    const GAP = 0.72;
    const GROW = 1.0;
    const ids = points.map((p) => p.cluster_id);
    const sums = new Map<string, { x: number; y: number; n: number }>();
    ids.forEach((id, i) => {
      const s = sums.get(id) ?? { x: 0, y: 0, n: 0 };
      s.x += xs[i];
      s.y += ys[i];
      s.n += 1;
      sums.set(id, s);
    });
    const centres = new Map([...sums].map(([id, s]) => [id, { x: s.x / s.n, y: s.y / s.n }]));
    const named = [...centres].filter(([id]) => id !== UNCLUSTERED_ID).map(([, c]) => c);
    const mid = {
      x: named.reduce((a, c) => a + c.x, 0) / (named.length || 1),
      y: named.reduce((a, c) => a + c.y, 0) / (named.length || 1),
    };
    const tx = xs.map((x, i) => {
      const c = centres.get(ids[i])!;
      return mid.x + (c.x - mid.x) * GAP + (x - c.x) * GROW;
    });
    const ty = ys.map((y, i) => {
      const c = centres.get(ids[i])!;
      return mid.y + (c.y - mid.y) * GAP + (y - c.y) * GROW;
    });
    const fit = (v: number[]): number[] => {
      const lo = Math.min(...v);
      const hi = Math.max(...v);
      return v.map((a) => (a - lo) / (hi - lo || 1));
    };
    const fx = fit(tx);
    const fy = fit(ty);
    return points.map((p, i) => ({
      px: PAD + fx[i] * (size.w - 2 * PAD),
      py: size.h - PAD - fy[i] * (size.h - 2 * PAD),
      p,
    }));
  }, [points, size]);

  // A soft territory per narrative: centroid plus the 80th percentile distance of its points
  const hulls = useMemo<Hull[]>(() => {
    const groups = new Map<string, Placed[]>();
    for (const q of placed) {
      if (q.p.cluster_id === UNCLUSTERED_ID) continue;
      groups.set(q.p.cluster_id, [...(groups.get(q.p.cluster_id) ?? []), q]);
    }
    return [...groups.entries()].map(([id, qs]) => {
      const x = qs.reduce((s, q) => s + q.px, 0) / qs.length;
      const y = qs.reduce((s, q) => s + q.py, 0) / qs.length;
      const d = qs.map((q) => Math.hypot(q.px - x, q.py - y)).sort((a, b) => a - b);
      return { id, x, y, r: (d[Math.floor((d.length - 1) * 0.8)] ?? 0) + 14 };
    });
  }, [placed]);

  const labelled = useMemo(() => {
    const counts = new Map(narratives.map((n) => [n.cluster_id, n.n_total]));
    return [...hulls].sort((a, b) => (counts.get(b.id) ?? 0) - (counts.get(a.id) ?? 0)).slice(0, LABELLED);
  }, [hulls, narratives]);

  const active = hover?.id ?? listHover ?? selectedId;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !size.w) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size.w * dpr;
    canvas.height = size.h * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);

    for (const h of hulls) {
      const on = active === h.id;
      const dim = active != null && !on;
      const color = clusterColor(h.id);
      ctx.globalAlpha = dim ? 0.3 : 1;
      ctx.beginPath();
      ctx.arc(h.x, h.y, h.r, 0, Math.PI * 2);
      ctx.fillStyle = color.replace(')', ` / ${on ? 0.16 : 0.07})`);
      ctx.fill();
      ctx.lineWidth = on ? 1.4 : 1;
      ctx.strokeStyle = color.replace(')', ` / ${on ? 0.7 : 0.28})`);
      ctx.stroke();
    }

    for (const { px, py, p } of placed) {
      const dim = active != null && p.cluster_id !== active;
      const color = clusterColor(p.cluster_id);
      ctx.globalAlpha = dim ? 0.14 : p.resolved ? 0.95 : 0.6;
      ctx.beginPath();
      ctx.arc(px, py, p.resolved ? 3.6 : 2.8, 0, Math.PI * 2);
      if (p.reached_30k) {
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.lineWidth = p.resolved ? 1.4 : 1;
        ctx.strokeStyle = color;
        if (!p.resolved) ctx.setLineDash([1.5, 1.5]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    const taken: { x0: number; x1: number; y0: number; y1: number }[] = [];
    for (const h of labelled) {
      const n = byId.get(h.id);
      if (!n) continue;
      const text = n.keywords.slice(0, 2).join(' / ') || n.label;
      const w = ctx.measureText(text).width + 14;
      const x = Math.min(Math.max(h.x, w / 2 + 4), size.w - w / 2 - 4);
      const y = Math.max(h.y - h.r - 12, 14);
      const box = { x0: x - w / 2, x1: x + w / 2, y0: y - 10, y1: y + 10 };
      // Larger narratives are placed first; a label that would cover another is left to the side list
      if (taken.some((t) => box.x0 < t.x1 && box.x1 > t.x0 && box.y0 < t.y1 && box.y1 > t.y0)) continue;
      taken.push(box);
      ctx.globalAlpha = active != null && h.id !== active ? 0.3 : 1;
      ctx.fillStyle = 'rgba(35,23,7,.88)';
      ctx.beginPath();
      ctx.roundRect(x - w / 2, y - 10, w, 20, 6);
      ctx.fill();
      ctx.fillStyle = '#FEFAF0';
      ctx.fillText(text, x, y);
    }
    ctx.globalAlpha = 1;
  }, [placed, hulls, labelled, size, active, byId]);

  const nearest = useCallback(
    (clientX: number, clientY: number): { id: string; x: number; y: number } | null => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      let best: Placed | null = null;
      let bestD = HIT_RADIUS * HIT_RADIUS;
      for (const q of placed) {
        const d = (q.px - x) ** 2 + (q.py - y) ** 2;
        if (d < bestD) {
          bestD = d;
          best = q;
        }
      }
      return best ? { id: best.p.cluster_id, x, y } : null;
    },
    [placed],
  );

  const hoverNarrative = hover ? byId.get(hover.id) : undefined;
  const unclusteredHover = hover?.id === UNCLUSTERED_ID;

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 md:px-5 py-3 border-b border-[var(--rule)]">
        <p className="text-[13px] text-[var(--dim)]">
          {points.length.toLocaleString('en-US')} tokens, one dot each. Position reflects lore similarity.
        </p>
        <ul aria-label="Map legend" className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-[var(--dim)]">
          {MAP_LEGEND.map((l) => (
            <li key={l.key} className="inline-flex items-center gap-1.5">
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                {l.key === 'reached' && <circle cx="6" cy="6" r="4.5" fill="currentColor" />}
                {l.key === 'stalled' && <circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" strokeWidth="1.5" />}
                {l.key === 'pending' && <circle cx="6" cy="6" r="3.5" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="1.5 1.5" />}
              </svg>
              {l.text}
            </li>
          ))}
        </ul>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div ref={wrapRef} className="relative w-full min-w-0 bg-[var(--panel2)]" style={{ height: size.h || 340 }}>
          <div aria-hidden className="pointer-events-none absolute inset-0 epochs-grid opacity-60" />
          <canvas
            ref={canvasRef}
            role="img"
            aria-label={`Narrative map: ${points.length} tokens as anonymous points in ${narratives.length} narratives. The list beside it and the table below cover the same narratives.`}
            className="relative block w-full h-full cursor-crosshair touch-pan-y"
            style={{ width: size.w || '100%', height: size.h || 340 }}
            onPointerMove={(e) => setHover(nearest(e.clientX, e.clientY))}
            onPointerLeave={() => setHover(null)}
            onClick={(e) => {
              const hit = nearest(e.clientX, e.clientY);
              if (hit && hit.id !== UNCLUSTERED_ID) onSelect(hit.id);
            }}
          />
          {hover && (hoverNarrative || unclusteredHover) && (
            <div
              role="status"
              className="pointer-events-none absolute z-10 max-w-[240px] rounded-lg border border-[var(--border-strong)] bg-[var(--panel)] px-3 py-2 shadow-lg"
              style={{ left: Math.min(hover.x + 14, Math.max(size.w - 250, 4)), top: Math.min(hover.y + 14, size.h - 84) }}
            >
              <div className="font-mono text-[11.5px] font-semibold text-[var(--fg-hi)]">
                {unclusteredHover ? 'Unclustered' : hoverNarrative!.label}
              </div>
              {hoverNarrative && (
                <div className="mt-1 font-mono text-[11px] text-[var(--dim)] leading-snug">
                  {hoverNarrative.survival_rate != null
                    ? `${fmtPct(hoverNarrative.survival_rate)} reached $30K (95% ${fmtCi(hoverNarrative.ci)})`
                    : NO_DATA(hoverNarrative.n_resolved)}
                  <br />n = {hoverNarrative.n_resolved} resolved, {hoverNarrative.n_total} tokens
                </div>
              )}
              {unclusteredHover && <div className="mt-1 font-mono text-[11px] text-[var(--dim)]">Tokens that fit no narrative.</div>}
            </div>
          )}
        </div>

        <ul aria-label="Narratives on the map" className="border-t lg:border-t-0 lg:border-l border-[var(--rule)] divide-y divide-[var(--rule)] lg:max-h-[480px] overflow-y-auto">
          {narratives.map((n) => (
            <li key={n.cluster_id}>
              <button
                type="button"
                aria-pressed={selectedId === n.cluster_id}
                onClick={() => onSelect(n.cluster_id)}
                onMouseEnter={() => setListHover(n.cluster_id)}
                onMouseLeave={() => setListHover(null)}
                onFocus={() => setListHover(n.cluster_id)}
                onBlur={() => setListHover(null)}
                className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-white/[0.04] focus-visible:bg-white/[0.06] aria-pressed:bg-[var(--banana-glow)] transition-colors"
              >
                <span aria-hidden className="mt-1 w-3 h-3 rounded-sm shrink-0" style={{ background: clusterColor(n.cluster_id) }} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium text-[var(--fg-hi)] break-words">{n.label}</span>
                  <span className="mt-0.5 block font-mono text-[11px] text-[var(--dim)]">
                    {n.survival_rate != null ? `${fmtPct(n.survival_rate)} reached` : 'Low data'}, n = {n.n_resolved}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};
