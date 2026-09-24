import { useEffect, useState } from 'react';

import { ApiError } from './api/client';
import type { AnalysisStatus } from './api/types';
import { api, POLL_INTERVAL_MS } from './config';

export const isTerminal = (status: AnalysisStatus['status']) =>
  status === 'COMPLETED' || status === 'FAILED';

const MAX_POLL_BACKOFF_MS = 30_000;

export function useAnalysisStatus(id: string) {
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let transientFailures = 0;

    const poll = async () => {
      let delay = POLL_INTERVAL_MS;
      try {
        const next = await api.getAnalysis(id);
        if (cancelled) return;
        setStatus(next);
        setError(null);
        transientFailures = 0;
        if (isTerminal(next.status)) return;
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));

        if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429) {
          return;
        }

        transientFailures += 1;
        const backoff = Math.min(
          POLL_INTERVAL_MS * 2 ** Math.min(transientFailures, 4),
          MAX_POLL_BACKOFF_MS,
        );
        delay =
          e instanceof ApiError && e.status === 429
            ? Math.max(backoff, e.retryAfterMs ?? 0)
            : backoff;
      }
      timer = setTimeout(poll, delay);
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id]);

  return { status, error };
}
