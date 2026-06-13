import type { CandleInterval } from "../../api/client";

export type MarketCandle = {
  symbol: string;
  interval: CandleInterval;
  open_time: string;
  close_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
};

export type MarketIndicatorPoint = {
  symbol: string;
  interval: CandleInterval;
  candle_time: string;
  rsi: number | null;
  rsi_ema: number | null;
  rsi_ema_diff: number | null;
  bollinger: {
    upper: number | null;
    middle: number | null;
    lower: number | null;
  };
};

export type StreamStatus = "connecting" | "connected" | "reconnecting" | "closed";

export type ChartComparisonLine = {
  id: string;
  price: number;
  title: string;
  color: string;
};
