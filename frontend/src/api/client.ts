export type Candle = {
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

export type CandleInterval = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d";

export type IndicatorPoint = {
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

export type HealthStatus = {
  status: "ok" | "degraded";
  time: string;
  checks: Record<string, { ok: boolean; error?: string }>;
};

export type ServiceHealth = {
  name: string;
  state: string;
  last_update: string;
  last_error: string | null;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthStatus>("/api/health"),
  services: () => request<ServiceHealth[]>("/api/status/services"),
  candles: (interval: CandleInterval, limit = 300) =>
    request<Candle[]>(`/api/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}`),
  candlesRange: (interval: CandleInterval, startMs: number, endMs: number, limit = 1000) =>
    request<Candle[]>(
      `/api/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}&start_ms=${startMs}&end_ms=${endMs}`
    ),
  indicators: (interval: CandleInterval, limit = 300) =>
    request<IndicatorPoint[]>(`/api/indicators?symbol=BTCUSDT&interval=${interval}&limit=${limit}`),
  marketWsUrl: (interval: CandleInterval) => {
    const base = API_BASE_URL || window.location.origin;
    const url = new URL("/api/ws/market", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("symbol", "BTCUSDT");
    url.searchParams.set("interval", interval);
    return url.toString();
  }
};
