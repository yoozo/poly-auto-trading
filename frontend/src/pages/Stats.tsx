import { useCallback, useState } from "react";
import { api, type StatsSummary } from "../api/client";
import { usePolling } from "../hooks/usePolling";

export default function Stats() {
  const [stats, setStats] = useState<StatsSummary | null>(null);
  const { lastRefresh, error } = usePolling(
    useCallback(async () => {
      setStats(await api.stats());
    }, [])
  );

  if (!stats) {
    return <div className="panel skeleton">Loading statistics...</div>;
  }

  return (
    <div className="page-stack">
      <section className="summary-grid stats-grid">
        <div className="kpi">
          <span>Signals</span>
          <strong>{stats.signals_total}</strong>
        </div>
        <div className="kpi">
          <span>Blocked</span>
          <strong>{stats.signals_blocked}</strong>
        </div>
        <div className="kpi">
          <span>Win rate</span>
          <strong>{(stats.win_rate * 100).toFixed(1)}%</strong>
        </div>
        <div className="kpi">
          <span>Avg spread</span>
          <strong>{stats.average_spread.toFixed(3)}</strong>
        </div>
        <div className="kpi">
          <span>Fill latency</span>
          <strong>{stats.average_fill_latency_ms}ms</strong>
        </div>
        <div className="kpi">
          <span>Dry-run PnL</span>
          <strong>{stats.dry_run_pnl_usdc.toFixed(2)} USDC</strong>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Phase 1.1</p>
            <h2>Statistics placeholder</h2>
          </div>
          <span className="metric">{lastRefresh ? lastRefresh.toLocaleTimeString() : new Date(stats.updated_at).toLocaleTimeString()}</span>
        </div>
        {error && <p className="error-text">{error}</p>}
        <p className="body-copy">
          This view is wired to the API and ready for persisted signal, order, fill latency, spread, and PnL summaries once the data services are connected.
        </p>
      </section>
    </div>
  );
}
