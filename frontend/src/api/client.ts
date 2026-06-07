export type BotStatus = {
  ws: Record<string, string>;
  scheduler: string;
  tracked_markets: number;
  last_error: string | null;
  updated_at: string;
  config: {
    symbol: string;
    dry_run: boolean;
    trading_enabled: boolean;
    max_order_usdc: number;
    max_daily_loss_usdc: number;
  };
};

export type PolyMarket = {
  id: string;
  title: string;
  interval: string;
  condition_id: string;
  yes_token_id: string;
  no_token_id: string;
  end_time: string | null;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  liquidity: number | null;
  status: string;
  event_id?: string | null;
  event_slug?: string | null;
  event_title?: string | null;
  outcomes?: string[];
  outcome_prices?: Array<number | null>;
  winning_outcome?: string | null;
  result_status?: "open" | "pending" | "resolved";
};

export type MarketResult = {
  event_slug: string;
  market_id: string | null;
  title: string;
  end_time: string | null;
  outcomes: string[];
  outcome_prices: Array<number | null>;
  winning_outcome: string | null;
  result_status: "open" | "pending" | "resolved";
};

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

export type Signal = {
  id: string;
  market_id: string;
  signal_type?: string;
  side: string;
  confidence: number;
  reason: string;
  risk_blocked: boolean;
  created_at: string;
  indicator_snapshot: Record<string, string | number>;
};

export type PreviewSignal = {
  id: string;
  symbol: string;
  side: "BUY_YES" | "BUY_NO" | "HOLD";
  confidence: number;
  reason: string;
  actionable: boolean;
  uses_closed_candle: boolean;
  created_at: string;
  source: string;
  indicator_snapshot: Record<string, string | number | boolean | null>;
};

export type Order = {
  id: string;
  market_id: string;
  side: string;
  price: number;
  size: number;
  filled_size: number;
  status: string;
  updated_at: string;
};

export type Notification = {
  id: string;
  event_type: string;
  message: string;
  status: string;
  sent_at: string;
};

export type Orderbook = {
  token_id: string;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  liquidity: number | null;
  updated_at: string | null;
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
};

export type Indicators = {
  symbol: string;
  updated_at: string | null;
  intervals: Record<
    string,
    {
      rsi: number | null;
      trend: string;
      bollinger: { upper: number | null; middle: number | null; lower: number | null };
    }
  >;
};

export type StatsSummary = {
  signals_total: number;
  signals_blocked: number;
  win_rate: number;
  average_spread: number;
  average_fill_latency_ms: number;
  dry_run_pnl_usdc: number;
  updated_at: string;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<BotStatus>("/status"),
  markets: () => request<PolyMarket[]>("/markets"),
  marketResult: (eventSlug: string) => request<MarketResult>(`/markets/result?event_slug=${encodeURIComponent(eventSlug)}`),
  candles: (interval: CandleInterval, limit = 120) =>
    request<Candle[]>(`/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}`),
  indicators: () => request<Indicators>("/indicators/latest?symbol=BTCUSDT"),
  orderbook: (tokenId?: string) =>
    request<Orderbook>(`/orderbook/latest${tokenId ? `?token_id=${tokenId}` : ""}`),
  latestSignal: () => request<Signal>("/signals/latest"),
  previewSignal: () => request<PreviewSignal>("/signals/preview"),
  signals: () => request<Signal[]>("/signals?limit=16"),
  orders: () => request<Order[]>("/orders"),
  notifications: () => request<Notification[]>("/notifications"),
  stats: () => request<StatsSummary>("/stats/summary")
};

export type CandleInterval = "1m" | "5m" | "15m" | "30m" | "1h" | "4h";
export const candleIntervals: CandleInterval[] = ["1m", "5m", "15m", "30m", "1h", "4h"];
