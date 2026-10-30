import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Load data on mount (and on demand), optionally polling while ``pollWhile`` holds. */
export function useAsync<T>(
  load: () => Promise<T>,
  deps: unknown[],
  options: { pollMs?: number; pollWhile?: (data: T) => boolean } = {},
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Something went wrong loading this page.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    setLoading(true);
    void reload();
  }, [reload]);

  const { pollMs, pollWhile } = options;
  useEffect(() => {
    if (!pollMs || !data || !pollWhile?.(data)) return;
    const timer = window.setTimeout(() => void reload(), pollMs);
    return () => window.clearTimeout(timer);
  }, [data, pollMs, pollWhile, reload]);

  return { data, error, loading, reload };
}

/** Run a user action, tracking busy and error state for the button that triggered it. */
export function useAction<A extends unknown[], R>(action: (...args: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setBusy(true);
      setError(null);
      try {
        return await action(...args);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "That didn't work. Try again.");
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [action],
  );
  return { run, busy, error };
}
