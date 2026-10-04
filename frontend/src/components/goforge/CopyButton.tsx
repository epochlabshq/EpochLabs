'use client';

import React, { useEffect, useRef, useState } from 'react';

/** Copies `value` to the clipboard. Stops the click so it never opens the card behind it. */
export const CopyButton: React.FC<{ value: string; label: string }> = ({ value, label }) => {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard blocked: the value is still selectable text */
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={done ? `${label} copied` : `Copy ${label}`}
      className="shrink-0 rounded-md border border-[var(--border-strong)] px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-wider text-[var(--dim)] hover:text-[var(--banana)] hover:border-[var(--banana)] transition-colors"
    >
      <span aria-live="polite">{done ? 'Copied' : 'Copy'}</span>
    </button>
  );
};
