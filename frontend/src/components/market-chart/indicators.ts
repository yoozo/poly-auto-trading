import type { CandleInterval } from "../../api/client";
import type { MarketCandle, MarketIndicatorPoint } from "./types";

const RSI_PERIOD = 14;
const RSI_EMA_PERIOD = 14;
const BOLLINGER_PERIOD = 20;
const BOLLINGER_STDDEV_MULTIPLIER = 2;

export const INDICATOR_WARMUP_BARS = 80;

export function calculateIndicatorPoints(candles: MarketCandle[], interval: CandleInterval): MarketIndicatorPoint[] {
  if (candles.length === 0) return [];

  const closes = candles.map((candle) => candle.close);
  const rsiValues = calculateRsiSeries(closes, RSI_PERIOD);
  const rsiEmaValues = calculateNullableEmaSeries(rsiValues, RSI_EMA_PERIOD);
  const bollingerValues = calculateBollingerSeries(closes);

  return candles.map((candle, index) => {
    const rsi = rsiValues[index];
    const rsiEma = rsiEmaValues[index];
    const rsiEmaDiff = rsi !== null && rsiEma !== null ? rsi - rsiEma : null;
    return {
      symbol: candle.symbol,
      interval,
      candle_time: candle.open_time,
      rsi: roundNullable(rsi),
      rsi_ema: roundNullable(rsiEma),
      rsi_ema_diff: roundNullable(rsiEmaDiff),
      bollinger: bollingerValues[index],
    };
  });
}

function calculateRsiSeries(closes: number[], period: number): Array<number | null> {
  if (closes.length <= period) return Array(closes.length).fill(null);

  const values: Array<number | null> = Array(closes.length).fill(null);
  const gains: number[] = [];
  const losses: number[] = [];

  for (let index = 0; index < period; index += 1) {
    const change = closes[index + 1] - closes[index];
    gains.push(Math.max(change, 0));
    losses.push(Math.max(-change, 0));
  }

  let averageGain = mean(gains);
  let averageLoss = mean(losses);
  values[period] = rsiFromAverageGainLoss(averageGain, averageLoss);

  for (let index = period + 1; index < closes.length; index += 1) {
    const change = closes[index] - closes[index - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);
    averageGain = (averageGain * (period - 1) + gain) / period;
    averageLoss = (averageLoss * (period - 1) + loss) / period;
    values[index] = rsiFromAverageGainLoss(averageGain, averageLoss);
  }

  return values;
}

function rsiFromAverageGainLoss(averageGain: number, averageLoss: number) {
  if (averageLoss === 0) return 100;
  const relativeStrength = averageGain / averageLoss;
  return 100 - 100 / (1 + relativeStrength);
}

function calculateNullableEmaSeries(values: Array<number | null>, period: number): Array<number | null> {
  const output: Array<number | null> = Array(values.length).fill(null);
  const seededValues: number[] = [];
  let ema: number | null = null;
  const multiplier = 2 / (period + 1);

  values.forEach((value, index) => {
    if (value === null) return;
    if (ema === null) {
      seededValues.push(value);
      if (seededValues.length < period) return;
      ema = mean(seededValues.slice(-period));
    } else {
      ema = (value - ema) * multiplier + ema;
    }
    output[index] = ema;
  });

  return output;
}

function calculateBollingerSeries(closes: number[]): MarketIndicatorPoint["bollinger"][] {
  const values: MarketIndicatorPoint["bollinger"][] = [];
  let rollingSum = 0;
  let rollingSquareSum = 0;

  for (let index = 0; index < closes.length; index += 1) {
    const close = closes[index];
    rollingSum += close;
    rollingSquareSum += close * close;
    if (index >= BOLLINGER_PERIOD) {
      const expired = closes[index - BOLLINGER_PERIOD];
      rollingSum -= expired;
      rollingSquareSum -= expired * expired;
    }
    if (index + 1 < BOLLINGER_PERIOD) {
      values.push({ upper: null, middle: null, lower: null });
      continue;
    }
    const middle = rollingSum / BOLLINGER_PERIOD;
    const variance = Math.max(0, rollingSquareSum / BOLLINGER_PERIOD - middle * middle);
    const deviation = Math.sqrt(variance);
    values.push({
      upper: round(middle + deviation * BOLLINGER_STDDEV_MULTIPLIER),
      middle: round(middle),
      lower: round(middle - deviation * BOLLINGER_STDDEV_MULTIPLIER),
    });
  }

  return values;
}

function mean(values: number[]) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function roundNullable(value: number | null) {
  return value === null ? null : round(value);
}

function round(value: number) {
  return Math.round(value * 10_000) / 10_000;
}
