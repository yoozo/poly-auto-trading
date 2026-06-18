import type { CandleInterval } from "../../api/client";
import type { MarketCandle } from "./types";
import { candleTime } from "./utils";

export type ChartCandle = MarketCandle & {
  chartTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type ChartCandlestickData = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type CandlestickBuildResult = {
  candles: ChartCandle[];
  data: ChartCandlestickData[];
  rejectedCount: number;
};

export function buildCandlestickData(candles: MarketCandle[], interval: CandleInterval): CandlestickBuildResult {
  const byTime = new Map<number, ChartCandle>();
  let rejectedCount = 0;

  for (const candle of candles) {
    const chartCandle = normalizeChartCandle(candle, interval);
    if (!chartCandle) {
      rejectedCount += 1;
      continue;
    }
    byTime.set(chartCandle.chartTime, chartCandle);
  }

  const chartCandles = Array.from(byTime.values()).sort((left, right) => left.chartTime - right.chartTime);
  const data = chartCandles.map((candle) => ({
    time: candle.chartTime,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }));

  return { candles: chartCandles, data, rejectedCount };
}

function normalizeChartCandle(candle: MarketCandle, interval: CandleInterval): ChartCandle | null {
  if (candle.interval !== interval) return null;
  const time = candleTime(candle);
  const open = finiteCandleValue(candle.open);
  const high = finiteCandleValue(candle.high);
  const low = finiteCandleValue(candle.low);
  const close = finiteCandleValue(candle.close);

  if (!Number.isFinite(time) || time <= 0 || open === null || high === null || low === null || close === null) {
    return null;
  }
  if (high < Math.max(open, close, low) || low > Math.min(open, close, high)) {
    return null;
  }

  // 主图 series 只接收这里标准化后的 OHLC，避免原始 API/WS 字段污染 lightweight-charts。
  return { ...candle, chartTime: time, open, high, low, close };
}

function finiteCandleValue(value: unknown) {
  if (value === null || value === undefined || value === "") return null;
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}
