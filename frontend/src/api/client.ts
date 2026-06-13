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
  metadata: Record<string, unknown>;
};

export type ServiceEventRecord = {
  id: number;
  service: string;
  level: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type SignalRecord = {
  id: number;
  signal_key: string;
  signal_label: string;
  action: "buy" | "sell" | "hold";
  direction: "long" | "short" | "neutral";
  target_type: string;
  target_key: string;
  dedupe_key: string;
  occurred_at: string;
  score: number | null;
  input_snapshot: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type NotificationDeliveryStatus = "sent" | "skipped_disabled" | "error";

export type NotificationDelivery = {
  id: number;
  channel: string;
  delivery_key: string;
  target_type: string;
  target_key: string;
  status: NotificationDeliveryStatus;
  title: string;
  message: string;
  error: string;
  sent_at: string | null;
  created_at: string;
  updated_at: string;
  signals: SignalRecord[];
};

export type TelegramStatus = {
  configured: boolean;
  enabled: boolean;
  chat_id_masked: string | null;
  missing: string[];
  last_delivery: NotificationDelivery | null;
};

export type ReportTask = {
  id: string;
  account_id: string | null;
  status: "running" | "done" | "error";
  message: string;
  percent: number;
  result: Record<string, unknown>;
  error: string;
  created_at: string | null;
  updated_at: string | null;
};

export type ReportAccount = {
  id: string;
  input: string;
  normalized_user: string;
  proxy_wallet: string;
  profile: Record<string, unknown>;
  favorite: boolean;
  note: string;
  last_downloaded_at: string | null;
  activity_count: number;
  latest_activity_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type RecentPerformance = {
  days: number;
  market_count: number;
  settled_market_count: number;
  unsettled_market_count: number;
  cost: number;
  recovery: number;
  pnl: number;
  roi: number | null;
  win_rate: number | null;
  unsettled_exposure: number;
};

export type AccountSummary = {
  account_id: string;
  activity_count: number;
  market_count: number;
  data_start: string | null;
  data_end: string | null;
  generated_at: string;
  total_cost: number;
  total_recovery: number;
  total_pnl: number;
  total_pnl_with_rebate: number;
  total_roi: number | null;
  maker_rebate_count: number;
  maker_rebate_amount: number;
  settled_market_count: number;
  unsettled_market_count: number;
  unsettled_exposure: number;
  win_market_count: number;
  loss_market_count: number;
  breakeven_market_count: number;
  win_rate: number | null;
  average_cost: number | null;
  median_cost: number | null;
  max_cost: number | null;
  average_profit: number | null;
  average_loss: number | null;
  incomplete_market_count: number;
  recent: RecentPerformance[];
  daily_last_7d: Array<{ date: string; cost: number; recovery: number; pnl: number; roi: number | null }>;
};

export type MarketPerformance = {
  market_id: string;
  title: string;
  slug: string | null;
  condition_id: string | null;
  event_slug: string | null;
  result: string;
  position_status: string;
  activity_count: number;
  redeem_count: number;
  merge_count: number;
  market_date: string | null;
  redeem_time: string | null;
  up_cost: number;
  up_shares: number;
  up_average_cost: number | null;
  down_cost: number;
  down_shares: number;
  down_average_cost: number | null;
  cost: number;
  recovery: number;
  merge_return: number;
  maker_rebate: number;
  pnl: number;
  pnl_with_rebate: number;
  roi: number | null;
  if_up_pnl: number | null;
  if_up_roi: number | null;
  if_down_pnl: number | null;
  if_down_roi: number | null;
  incomplete: boolean;
};

export type MarketPerformancePage = {
  items: MarketPerformance[];
  total: number;
  offset: number;
  limit: number;
};

export type PolymarketOrderLevel = {
  price: number | null;
  size: number | null;
};

export type PolymarketOutcomeQuote = {
  name: string;
  token_id: string | null;
  price: number | null;
  buy_price: number | null;
  sell_price: number | null;
  best_bid: number | null;
  best_ask: number | null;
  last_trade_price: number | null;
  updated_at: string | null;
  bids: PolymarketOrderLevel[];
  asks: PolymarketOrderLevel[];
};

export type PolymarketUpDownMarket = {
  id: string;
  condition_id: string | null;
  slug: string | null;
  title: string;
  series_slug: string | null;
  interval: PolymarketInterval;
  start_time: string | null;
  end_time: string | null;
  window: "current" | "next" | "upcoming" | "expired" | "unknown";
  seconds_to_start: number | null;
  seconds_to_end: number | null;
  accepting_orders: boolean;
  volume: number | null;
  liquidity: number | null;
  updated_at: string | null;
  outcome_quotes: PolymarketOutcomeQuote[];
};

export type PolymarketInterval = "5m" | "15m" | "1h" | "4h";

export type PolymarketWsMessage = {
  type: "polymarket.btc_up_down.snapshot";
  interval: PolymarketInterval;
  markets: PolymarketUpDownMarket[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // Keep the HTTP status fallback.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthStatus>("/api/health"),
  services: () => request<ServiceHealth[]>("/api/status/services"),
  serviceEvents: (
    params: {
      service?: string;
      level?: string;
      limit?: number;
      start?: string;
      end?: string;
    } = {},
  ) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? 100));
    if (params.service) query.set("service", params.service);
    if (params.level) query.set("level", params.level);
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<ServiceEventRecord[]>(`/api/status/events?${query.toString()}`);
  },
  telegramStatus: () => request<TelegramStatus>("/api/notifications/telegram/status"),
  updateTelegramStatus: (enabled: boolean) =>
    request<TelegramStatus>("/api/notifications/telegram/status", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  testTelegram: () =>
    request<{ ok: boolean; message: string }>("/api/notifications/telegram/test", {
      method: "POST",
    }),
  notificationDeliveries: (targetKey?: string, limit = 20) => {
    const query = new URLSearchParams();
    query.set("limit", String(limit));
    if (targetKey) query.set("target_key", targetKey);
    return request<NotificationDelivery[]>(`/api/notifications/deliveries?${query.toString()}`);
  },
  signals: (targetKey?: string, limit = 20) => {
    const query = new URLSearchParams();
    query.set("limit", String(limit));
    if (targetKey) query.set("target_key", targetKey);
    return request<SignalRecord[]>(`/api/signals?${query.toString()}`);
  },
  candles: (interval: CandleInterval, limit = 300) =>
    request<Candle[]>(`/api/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}`),
  candlesRange: (interval: CandleInterval, startMs: number, endMs: number, limit = 1000) =>
    request<Candle[]>(
      `/api/candles?symbol=BTCUSDT&interval=${interval}&limit=${limit}&start_ms=${startMs}&end_ms=${endMs}`
    ),
  indicators: (interval: CandleInterval, limit = 300) =>
    request<IndicatorPoint[]>(`/api/indicators?symbol=BTCUSDT&interval=${interval}&limit=${limit}`),
  indicatorsRange: (interval: CandleInterval, startMs: number, endMs: number, limit = 1000) =>
    request<IndicatorPoint[]>(
      `/api/indicators?symbol=BTCUSDT&interval=${interval}&limit=${limit}&start_ms=${startMs}&end_ms=${endMs}`
    ),
  analyzeAccount: (input: string, activityLimit: number) =>
    request<{ task_id: string; status: ReportTask["status"] }>("/api/reports/accounts/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input, activity_limit: activityLimit }),
    }),
  reportTask: (taskId: string) => request<ReportTask>(`/api/reports/tasks/${taskId}`),
  reportAccounts: () => request<ReportAccount[]>("/api/reports/accounts"),
  updateReportAccount: (accountId: string, payload: { note?: string; favorite?: boolean }) =>
    request<ReportAccount>(`/api/reports/accounts/${accountId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  accountSummary: (accountId: string) => request<AccountSummary>(`/api/reports/accounts/${accountId}/summary`),
  accountMarkets: (
    accountId: string,
    params: {
      offset?: number;
      limit?: number;
      search?: string;
      startDate?: string;
      endDate?: string;
      onlyBilateral?: boolean;
    } = {},
  ) => {
    const query = new URLSearchParams();
    query.set("offset", String(params.offset ?? 0));
    query.set("limit", String(params.limit ?? 20));
    if (params.search) query.set("search", params.search);
    if (params.startDate) query.set("start_date", params.startDate);
    if (params.endDate) query.set("end_date", params.endDate);
    if (params.onlyBilateral) query.set("only_bilateral", "true");
    return request<MarketPerformancePage>(`/api/reports/accounts/${accountId}/markets?${query.toString()}`);
  },
  polymarketBtcUpDown: (interval: PolymarketInterval = "5m", limit = 12) =>
    request<PolymarketUpDownMarket[]>(
      `/api/polymarket/btc-up-down?interval=${interval}&limit=${limit}&include_recent_closed=true`
    ),
  polymarketBtcUpDownWsUrl: (interval: PolymarketInterval) => {
    const base = API_BASE_URL || window.location.origin;
    const url = new URL("/api/ws/polymarket/btc-up-down", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("interval", interval);
    return url.toString();
  },
  marketWsUrl: (interval: CandleInterval) => {
    const base = API_BASE_URL || window.location.origin;
    const url = new URL("/api/ws/market", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("symbol", "BTCUSDT");
    url.searchParams.set("interval", interval);
    return url.toString();
  }
};
