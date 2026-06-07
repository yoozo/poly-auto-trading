import { useCallback, useState } from "react";
import { api, type PreviewSignal, type Signal } from "../api/client";
import SignalTimeline from "../components/SignalTimeline";
import { usePolling } from "../hooks/usePolling";

export default function Signals() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [preview, setPreview] = useState<PreviewSignal | null>(null);
  const { lastRefresh, error } = usePolling(
    useCallback(async () => {
      const [nextSignals, nextPreview] = await Promise.all([api.signals(), api.previewSignal()]);
      setSignals(nextSignals);
      setPreview(nextPreview);
    }, [])
  );

  const allowed = signals.filter((signal) => !signal.risk_blocked).length;

  return (
    <div className="page-stack">
      <section className="summary-grid">
        <div className="kpi">
          <span>Total</span>
          <strong>{signals.length}</strong>
        </div>
        <div className="kpi">
          <span>Allowed</span>
          <strong>{allowed}</strong>
        </div>
        <div className="kpi">
          <span>Blocked</span>
          <strong>{signals.length - allowed}</strong>
        </div>
        <div className="kpi">
          <span>Avg confidence</span>
          <strong>
            {signals.length
              ? `${((signals.reduce((sum, signal) => sum + signal.confidence, 0) / signals.length) * 100).toFixed(0)}%`
              : "-"}
          </strong>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Preview</p>
            <h2>Forming candle signal</h2>
          </div>
          <span className="metric">{preview?.actionable ? "Actionable" : "Watch only"} · {lastRefresh ? lastRefresh.toLocaleTimeString() : "loading"}</span>
        </div>
        {error && <p className="error-text">{error}</p>}
        {preview && (
          <div className="preview-signal">
            <strong>{preview.side}</strong>
            <p>{preview.reason}</p>
            <div className="mini-grid">
              <span>Confidence {(preview.confidence * 100).toFixed(0)}%</span>
              <span>{preview.uses_closed_candle ? "Closed candle" : "Unclosed candle"}</span>
              <span>{new Date(preview.created_at).toLocaleTimeString()}</span>
            </div>
          </div>
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Strategy</p>
            <h2>Signal timeline</h2>
          </div>
        </div>
        <SignalTimeline signals={signals} />
      </section>
    </div>
  );
}
