import type { Signal } from "../api/client";

type Props = {
  signals: Signal[];
};

export default function SignalTimeline({ signals }: Props) {
  return (
    <div className="timeline">
      {signals.map((signal) => (
        <article className="timeline-item" key={signal.id}>
          <div className={signal.risk_blocked ? "dot blocked" : "dot"} />
          <div>
            <div className="timeline-head">
              <strong>{signal.side}</strong>
              <span>{new Date(signal.created_at).toLocaleTimeString()}</span>
            </div>
            <p>{signal.reason}</p>
            <div className="mini-grid">
              <span>Confidence {(signal.confidence * 100).toFixed(0)}%</span>
              <span>{signal.signal_type ?? "signal"}</span>
              <span>{signal.market_id}</span>
              <span>{signal.risk_blocked ? "Risk blocked" : "Allowed"}</span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
