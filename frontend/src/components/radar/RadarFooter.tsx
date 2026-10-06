import React from 'react';
import { getApiBaseUrl } from '@/config/constants';
import { FOR_CARDS, METHODOLOGY_STEPS, RADAR_DISCLAIMER } from '@/config/radarCopy';
import type { RadarPayload } from './types';

export const ReadingCards: React.FC = () => (
  <section aria-label="How to read the radar" className="grid grid-cols-1 md:grid-cols-2 gap-4">
    {FOR_CARDS.map((c) => (
      <div key={c.title} className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
        <h2 className="font-sans font-semibold text-lg text-[var(--fg-hi)]">{c.title}</h2>
        <ul className="mt-3 space-y-2 text-[13.5px] text-[var(--dim)] leading-snug list-disc pl-4 marker:text-[var(--banana)]">
          {c.points.map((p) => <li key={p}>{p}</li>)}
        </ul>
      </div>
    ))}
  </section>
);

/** States the method that actually ran for the latest run, read from /api/radar. */
export const Methodology: React.FC<{ data: RadarPayload | null }> = ({ data }) => {
  const method =
    data?.method === 'kmeans' ? 'KMeans (k chosen by silhouette score, 8 to 25)'
    : data?.method === 'hdbscan' ? `HDBSCAN (minimum cluster size ${data.params.min_cluster_size ?? 15})`
    : 'HDBSCAN';
  const projection =
    data?.projection === 'umap' ? 'UMAP with a fixed seed'
    : data?.projection === 'pca' ? 'PCA (a fixed-sign 2D projection)'
    : 'a 2D projection';
  return (
    <section aria-labelledby="method-h" className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5 md:p-6">
      <h2 id="method-h" className="font-sans font-semibold text-xl md:text-2xl tracking-tight text-[var(--fg-hi)]">Methodology</h2>
      {data && (
        <p className="mt-2 text-[13.5px] text-[var(--fg)]">
          This run grouped lore with <strong className="text-[var(--fg-hi)]">{method}</strong> and drew the map with {projection}.
          {data.totals.n_lore_missing > 0 && ` ${data.totals.n_lore_missing.toLocaleString('en-US')} tokens have no lore and are not mapped.`}
        </p>
      )}
      <ol className="mt-4 space-y-2 text-[13.5px] text-[var(--dim)] leading-snug list-decimal pl-5 marker:text-[var(--banana)] marker:font-mono">
        {METHODOLOGY_STEPS.map((s) => <li key={s}>{s}</li>)}
      </ol>
      <p className="mt-4 text-[13px] text-[var(--dim)]">
        The label is the 48 hour outcome: reached $30K, or stalled. Raw data:{' '}
        <a href={`${getApiBaseUrl()}/api/radar`} target="_blank" rel="noopener noreferrer" className="font-mono text-[var(--banana)] hover:underline">/api/radar</a>
      </p>
    </section>
  );
};

export const RadarFooter: React.FC = () => (
  <section className="border-t border-[var(--rule)] bg-[var(--panel2)]">
    <div className="max-w-[1180px] mx-auto px-4 md:px-12 py-8">
      <p className="text-[var(--fg)] text-[14px] max-w-[64ch]">{RADAR_DISCLAIMER}</p>
    </div>
  </section>
);
