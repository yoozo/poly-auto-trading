import type { CandleInterval } from "../../api/client";
import type { MarketCandle, MarketIndicatorPoint } from "./types";

export type TimeValue = { time: number; value: number };

export function toUnixTime(value: string) {
  return Math.floor(new Date(value).getTime() / 1000);
}

export function candleTime(candle: MarketCandle) {
  return toUnixTime(candle.open_time);
}

export function indicatorTime(point: MarketIndicatorPoint) {
  return toUnixTime(point.candle_time);
}

export function intervalMs(interval: CandleInterval) {
  if (interval === "1m") return 60_000;
  if (interval === "5m") return 5 * 60_000;
  if (interval === "15m") return 15 * 60_000;
  if (interval === "30m") return 30 * 60_000;
  if (interval === "1h") return 60 * 60_000;
  if (interval === "4h") return 4 * 60 * 60_000;
  return 24 * 60 * 60_000;
}

export function initialLookbackMs(interval: CandleInterval) {
  if (interval === "1m") return 12 * 60 * 60_000;
  if (interval === "5m") return 3 * 24 * 60 * 60_000;
  if (interval === "15m") return 7 * 24 * 60 * 60_000;
  if (interval === "30m") return 14 * 24 * 60 * 60_000;
  if (interval === "1h") return 30 * 24 * 60 * 60_000;
  if (interval === "4h") return 120 * 24 * 60 * 60_000;
  return 365 * 24 * 60 * 60_000;
}

export function defaultVisibleBars(interval: CandleInterval) {
  if (interval === "1m") return 360;
  if (interval === "5m") return 320;
  if (interval === "15m") return 280;
  if (interval === "30m") return 240;
  if (interval === "1h") return 220;
  if (interval === "4h") return 200;
  return 180;
}

export function nearestTimeValue(points: TimeValue[], time: number) {
  if (!points.length) return null;
  let low = 0;
  let high = points.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const value = points[mid].time;
    if (value === time) return points[mid];
    if (value < time) low = mid + 1;
    else high = mid - 1;
  }
  const before = points[Math.max(0, high)];
  const after = points[Math.min(points.length - 1, low)];
  if (!before) return after ?? null;
  if (!after) return before;
  return Math.abs(before.time - time) <= Math.abs(after.time - time) ? before : after;
}

export function formatPrice(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function formatFixed(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return value.toFixed(2);
}

export function formatSigned(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

export function formatTooltipTime(time: number) {
  return new Date(time * 1000).toLocaleString();
}

export function mergeCandles(existing: MarketCandle[], incoming: MarketCandle[]) {
  const byKey = new Map<string, MarketCandle>();
  for (const candle of existing) {
    byKey.set(`${candle.symbol}:${candle.interval}:${candle.open_time}`, candle);
  }
  for (const candle of incoming) {
    byKey.set(`${candle.symbol}:${candle.interval}:${candle.open_time}`, candle);
  }
  return Array.from(byKey.values()).sort(
    (left, right) => new Date(left.open_time).getTime() - new Date(right.open_time).getTime()
  );
}
