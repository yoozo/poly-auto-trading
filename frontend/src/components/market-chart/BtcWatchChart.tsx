import MarketTechnicalChart, { type MarketTechnicalChartProps } from "./MarketTechnicalChart";
import type { StreamStatus } from "./types";

type BtcWatchChartProps = Omit<MarketTechnicalChartProps, "statusText"> & {
  latestStreamStatus: StreamStatus;
};

export default function BtcWatchChart({ latestStreamStatus, ...props }: BtcWatchChartProps) {
  return <MarketTechnicalChart {...props} statusText={`实时流 ${streamStatusLabel(latestStreamStatus)}`} />;
}

function streamStatusLabel(status: StreamStatus) {
  if (status === "connected") return "已连接";
  if (status === "reconnecting") return "重连中";
  if (status === "closed") return "已关闭";
  return "连接中";
}
