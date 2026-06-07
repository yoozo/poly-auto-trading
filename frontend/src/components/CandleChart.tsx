import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Candle } from "../api/client";

type Props = {
  candles: Candle[];
};

export default function CandleChart({ candles }: Props) {
  const data = candles.map((candle) => ({
    time: new Date(candle.close_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    close: candle.close,
    high: candle.high,
    low: candle.low
  }));

  return (
    <div className="chart-panel">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data}>
          <XAxis dataKey="time" minTickGap={28} tickLine={false} axisLine={false} />
          <YAxis domain={["dataMin - 120", "dataMax + 120"]} tickLine={false} axisLine={false} width={72} />
          <Tooltip contentStyle={{ borderRadius: 6, border: "1px solid #d7dde8" }} />
          <Line type="monotone" dataKey="close" stroke="#1d4ed8" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="high" stroke="#8aa4ce" strokeWidth={1} dot={false} />
          <Line type="monotone" dataKey="low" stroke="#c98c6b" strokeWidth={1} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

