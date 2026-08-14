import type { CandleInterval, MarketIndicatorPoint } from "../../api/client";

export type { MarketIndicatorPoint } from "../../api/client";

export type MarketCandle = {
  symbol: string;
  interval: CandleInterval;
  open_time: string;
  close_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  is_closed: boolean;
  source: "binance" | "chainlink" | "binance_fallback";
  is_complete: boolean;
  indicator?: MarketIndicatorPoint;
};

export type StreamStatus = "connecting" | "connected" | "reconnecting" | "closed";

export type ChartComparisonLine = {
  id: string;
  price: number;
  title: string;
  color: string;
};
