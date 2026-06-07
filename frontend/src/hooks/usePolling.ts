import { useEffect, useState } from "react";

export function usePolling(load: () => Promise<void>, intervalMs = 15000) {
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const run = () => {
      void load()
        .then(() => {
          if (!active) return;
          setLastRefresh(new Date());
          setError(null);
        })
        .catch((err: unknown) => {
          if (!active) return;
          setError(err instanceof Error ? err.message : String(err));
        });
    };

    run();
    const timer = window.setInterval(run, intervalMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [load, intervalMs]);

  return { lastRefresh, error };
}

