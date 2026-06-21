import type { CandleInterval, PolymarketInterval, PolymarketUpDownMarket } from "../api/client";
import type { MarketCandle } from "../components/market-chart/types";
import { intervalMs } from "../components/market-chart/utils";

export const ONE_MINUTE_MS = 60_000;
export const POLYMARKET_INTERVAL_MS: Record<PolymarketInterval, number> = {
  "5m": 5 * ONE_MINUTE_MS,
  "15m": 15 * ONE_MINUTE_MS,
  "1h": 60 * ONE_MINUTE_MS,
  "4h": 4 * 60 * ONE_MINUTE_MS,
};

const ET_TIME_ZONE = "America/New_York";
const MONTH_INDEX: Record<string, number> = {
  january: 0,
  february: 1,
  march: 2,
  april: 3,
  may: 4,
  june: 5,
  july: 6,
  august: 7,
  september: 8,
  october: 9,
  november: 10,
  december: 11,
};

export type PolymarketDisplayWindow = {
  startMs: number;
  endMs: number;
};

export type MarketComparisonTarget = {
  key: string;
  marketId: string;
  marketInterval: PolymarketInterval;
  baselineStartMs: number;
};

export function selectedPolymarketMarket({
  markets,
  selectedMarketId,
  selectedMarketSnapshot,
}: {
  markets: PolymarketUpDownMarket[];
  selectedMarketId: string | null;
  selectedMarketSnapshot: PolymarketUpDownMarket | null;
}) {
  if (selectedMarketId) {
    return (
      markets.find((market) => market.id === selectedMarketId) ??
      (selectedMarketSnapshot?.id === selectedMarketId ? selectedMarketSnapshot : undefined)
    );
  }
  return (
    markets.find((market) => market.window === "current") ??
    markets.find((market) => market.window === "next") ??
    markets[0]
  );
}

export function candleOpenAnchorMs(timeMs: number, interval: CandleInterval) {
  const stepMs = intervalMs(interval);
  if (!Number.isFinite(timeMs) || !Number.isFinite(stepMs) || stepMs <= 0) return timeMs;
  if (interval === "1w") {
    const binanceWeekAnchorMs = Date.UTC(1970, 0, 5);
    return Math.floor((timeMs - binanceWeekAnchorMs) / stepMs) * stepMs + binanceWeekAnchorMs;
  }
  return Math.floor(timeMs / stepMs) * stepMs;
}

function marketFocusTimeMs(window: PolymarketDisplayWindow, nowMs = Date.now()) {
  if (!Number.isFinite(nowMs)) return window.startMs;
  const latestWindowMs = Math.max(window.startMs, window.endMs - 1);
  return Math.min(Math.max(nowMs, window.startMs), latestWindowMs);
}

export function marketFocusAnchorMs(window: PolymarketDisplayWindow, interval: CandleInterval, nowMs = Date.now()) {
  return candleOpenAnchorMs(marketFocusTimeMs(window, nowMs), interval);
}

export function marketChartFocusKey({
  nonce,
  marketId,
  focusAnchorMs,
  candleInterval,
}: {
  nonce: number;
  marketId: string;
  focusAnchorMs: number;
  candleInterval: CandleInterval;
}) {
  return `polymarket-focus:${nonce}:${marketId}:${candleInterval}:${focusAnchorMs}`;
}

export function baselineStartMsForMarket(window: PolymarketDisplayWindow) {
  return candleOpenAnchorMs(window.startMs, "1m");
}

export function marketComparisonTarget(market: PolymarketUpDownMarket, nowMs = Date.now()): MarketComparisonTarget | null {
  const window = polymarketDisplayWindow(market);
  const startMs = window?.startMs ?? Number.NaN;
  if (!window || !Number.isFinite(startMs) || startMs > nowMs) return null;
  return {
    key: `${market.id}:${startMs}`,
    marketId: market.id,
    marketInterval: market.interval,
    baselineStartMs: baselineStartMsForMarket(window),
  };
}

export function hasCandleAtTime(rows: MarketCandle[], targetMs: number) {
  return rows.some((row) => {
    const openMs = new Date(row.open_time).getTime();
    return Number.isFinite(openMs) && openMs === targetMs;
  });
}

export function candleAtOpenTime(rows: MarketCandle[], startMs: number) {
  return (
    rows.find((row) => {
      const openMs = new Date(row.open_time).getTime();
      return Number.isFinite(openMs) && openMs === startMs;
    }) ?? null
  );
}

export function polymarketDisplayWindow(market: PolymarketUpDownMarket): PolymarketDisplayWindow | null {
  const titleWindow = polymarketTitleWindow(market);
  if (titleWindow) return titleWindow;

  const anchorMs = parseMarketTimeMs(market.start_time) ?? parseMarketTimeMs(market.end_time);
  const marketIntervalMs = POLYMARKET_INTERVAL_MS[market.interval];
  if (anchorMs == null || !Number.isFinite(anchorMs) || !marketIntervalMs) return null;
  const startMs = Math.floor(anchorMs / marketIntervalMs) * marketIntervalMs;
  return {
    startMs,
    endMs: startMs + marketIntervalMs,
  };
}

function polymarketTitleWindow(market: PolymarketUpDownMarket): PolymarketDisplayWindow | null {
  const rangeMatch = market.title.match(
    /([A-Za-z]+)\s+(\d{1,2}),\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*ET/i
  );
  const singleMatch = market.title.match(/([A-Za-z]+)\s+(\d{1,2}),\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*ET/i);
  const match = rangeMatch ?? singleMatch;
  if (!match) return null;
  const [, monthName, dayText, startHourText, startMinuteText, startPeriod] = match;
  const month = MONTH_INDEX[monthName.toLowerCase()];
  const anchorMs = parseMarketTimeMs(market.start_time) ?? parseMarketTimeMs(market.end_time);
  if (month == null || anchorMs == null) return null;

  const year = new Date(anchorMs).getUTCFullYear();
  const day = Number(dayText);
  const startWallHour = toTwentyFourHour(Number(startHourText), startPeriod);
  const startWallMinute = Number(startMinuteText ?? "0");
  if ([day, startWallHour, startWallMinute].some((value) => !Number.isFinite(value))) {
    return null;
  }

  // Polymarket 标题里的 ET 窗口才是合约比较区间；API 时间字段可能是开盘/展示偏移。
  const startMs = zonedWallTimeToUtcMs(year, month, day, startWallHour, startWallMinute, ET_TIME_ZONE);
  if (!rangeMatch) {
    return { startMs, endMs: startMs + POLYMARKET_INTERVAL_MS[market.interval] };
  }

  const endWallHour = toTwentyFourHour(Number(rangeMatch[6]), rangeMatch[8]);
  const endWallMinute = Number(rangeMatch[7] ?? "0");
  if ([endWallHour, endWallMinute].some((value) => !Number.isFinite(value))) return null;
  let endMs = zonedWallTimeToUtcMs(year, month, day, endWallHour, endWallMinute, ET_TIME_ZONE);
  if (endMs <= startMs) endMs += 24 * 60 * ONE_MINUTE_MS;
  return { startMs, endMs };
}

function toTwentyFourHour(hour: number, period: string) {
  const normalized = hour % 12;
  return period.toUpperCase() === "PM" ? normalized + 12 : normalized;
}

function zonedWallTimeToUtcMs(year: number, month: number, day: number, hour: number, minute: number, timeZone: string) {
  const utcGuess = Date.UTC(year, month, day, hour, minute, 0, 0);
  return utcGuess - timeZoneOffsetMs(timeZone, utcGuess);
}

function timeZoneOffsetMs(timeZone: string, utcMs: number) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "shortOffset",
  });
  const offset = formatter.formatToParts(new Date(utcMs)).find((part) => part.type === "timeZoneName")?.value ?? "GMT";
  const match = offset.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
  if (!match) return 0;
  const [, sign, hourText, minuteText] = match;
  const minutes = Number(hourText) * 60 + Number(minuteText ?? "0");
  return (sign === "-" ? -1 : 1) * minutes * ONE_MINUTE_MS;
}

function parseMarketTimeMs(value: string | null): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric)) {
    return numeric > 1e12 ? numeric : numeric * 1000;
  }
  const parsed = new Date(trimmed).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}
