import { CheckCircle2, CircleSlash, Clock3, Server } from "lucide-react";
import type { BotStatus } from "../api/client";

type Props = {
  status: BotStatus | null;
};

export default function StatusBar({ status }: Props) {
  if (!status) {
    return <div className="status-strip skeleton">Loading runtime status...</div>;
  }

  return (
    <div className="status-strip">
      {Object.entries(status.ws).map(([name, value]) => (
        <div className="status-pill" key={name}>
          {value === "connected" ? <CheckCircle2 size={16} /> : <CircleSlash size={16} />}
          <span>{name.split("_").join(" ")}</span>
          <strong>{value}</strong>
        </div>
      ))}
      <div className="status-pill">
        <Clock3 size={16} />
        <span>scheduler</span>
        <strong>{status.scheduler}</strong>
      </div>
      <div className="status-pill">
        <Server size={16} />
        <span>tracked</span>
        <strong>{status.tracked_markets}</strong>
      </div>
    </div>
  );
}
