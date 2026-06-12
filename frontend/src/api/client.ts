export type Candle = {
  symbol: string;
  interval: string;
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
    request<Candle[]>(`/api/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}`)
};
