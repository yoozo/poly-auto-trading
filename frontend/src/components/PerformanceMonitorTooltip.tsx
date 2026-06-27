import { Badge, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type MarketCandlesRequest,
  type MarketWsMessage,
  type PolymarketInterval,
  type PolymarketUpDownMarket,
  type PolymarketWsMessage,
} from "../api/client";

type PerformanceMetricKey = "ws_handshake" | "ws_ping" | "candles" | "polymarket";

type PerformanceMetricResult = {
  key: PerformanceMetricKey;
  title: string;
  latencyMs: number | null;
  status: "idle" | "running" | "ok" | "error";
  meta: string;
  error: string;
};

type PerformanceLatencyTone = "default" | "processing" | "success" | "warning" | "error";

const PERFORMANCE_INTERVAL: PolymarketInterval = "5m";
const DETECT_INTERVAL_MS = 60_000;
export const PERFORMANCE_MONITOR_ENABLED_KEY = "poly-auto.performanceMonitorEnabled";
export const PERFORMANCE_MONITOR_ENABLED_EVENT = "poly-auto.performanceMonitorEnabled.changed";

const DEFAULT_RESULTS: PerformanceMetricResult[] = [
  { key: "ws_handshake", title: "WS 握手", latencyMs: null, status: "idle", meta: "", error: "" },
  { key: "ws_ping", title: "Ping 延迟", latencyMs: null, status: "idle", meta: "", error: "" },
  { key: "candles", title: "币安 K线", latencyMs: null, status: "idle", meta: "BTCUSDT 5m", error: "" },
  { key: "polymarket", title: "Polymarket", latencyMs: null, status: "idle", meta: "5m markets", error: "" },
];

export function PerformanceMonitorTooltip() {
  const [results, setResults] = useState<PerformanceMetricResult[]>(DEFAULT_RESULTS);
  const [running, setRunning] = useState(false);
  const [enabled, setEnabled] = useState(() => readPerformanceMonitorEnabled());
  const runningRef = useRef(false);
  const mountedRef = useRef(true);

  const status = useMemo(() => {
    if (running) return "processing";
    return maxLatencyTone(
      results.filter((result) => result.key === "candles" || result.key === "polymarket")
    );
  }, [results, running]);

  const updateResult = useCallback((key: PerformanceMetricKey, patch: Partial<PerformanceMetricResult>) => {
    if (!mountedRef.current) return;
    setResults((current) => current.map((result) => (result.key === key ? { ...result, ...patch } : result)));
  }, []);

  const runTest = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    setResults(DEFAULT_RESULTS.map((result) => ({ ...result, status: "running", latencyMs: null, error: "" })));
    let socket: WebSocket | null = null;
    try {
      const handshakeStart = performance.now();
      socket = new WebSocket(api.marketWsUrl(PERFORMANCE_INTERVAL));
      const handshake = await waitForSocketOpen(socket, handshakeStart);
      updateResult("ws_handshake", handshake);
      if (handshake.status === "error") return;

      const ping = await measureMarketPing(socket);
      updateResult("ws_ping", ping);
      if (ping.status === "error") return;

      updateResult("candles", await measureMarketCandles(socket));
      updateResult("polymarket", await measurePolymarketOrderbook(PERFORMANCE_INTERVAL));
    } finally {
      socket?.close();
      runningRef.current = false;
      if (mountedRef.current) setRunning(false);
    }
  }, [updateResult]);

  useEffect(() => {
    mountedRef.current = true;
    const syncEnabled = () => setEnabled(readPerformanceMonitorEnabled());
    window.addEventListener("storage", syncEnabled);
    window.addEventListener(PERFORMANCE_MONITOR_ENABLED_EVENT, syncEnabled);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("storage", syncEnabled);
      window.removeEventListener(PERFORMANCE_MONITOR_ENABLED_EVENT, syncEnabled);
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setRunning(false);
      setResults(DEFAULT_RESULTS);
      return undefined;
    }
    void runTest();
    const timer = window.setInterval(() => {
      void runTest();
    }, DETECT_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, runTest]);

  if (!enabled) return null;

  return (
    <Tooltip
      classNames={{ root: "performance-tooltip-overlay" }}
      placement="bottomLeft"
      mouseEnterDelay={0.2}
      title={<PerformanceTooltipContent results={results} running={running} />}
    >
      <button
        className={`performance-tooltip-trigger performance-tooltip-trigger-${status}`}
        type="button"
        aria-label={running ? "性能检测中" : "重新检测性能"}
        onClick={() => void runTest()}
      >
        <span className="performance-tooltip-dot" />
      </button>
    </Tooltip>
  );
}

export function readPerformanceMonitorEnabled() {
  return localStorage.getItem(PERFORMANCE_MONITOR_ENABLED_KEY) === "1";
}

export function setPerformanceMonitorEnabled(enabled: boolean) {
  localStorage.setItem(PERFORMANCE_MONITOR_ENABLED_KEY, enabled ? "1" : "0");
  window.dispatchEvent(new Event(PERFORMANCE_MONITOR_ENABLED_EVENT));
}

function PerformanceTooltipContent({ results, running }: { results: PerformanceMetricResult[]; running: boolean }) {
  return (
    <div className="performance-tooltip-content">
      <div className="performance-tooltip-head">
        <Typography.Text strong>实时性能</Typography.Text>
        <Badge status={running ? "processing" : "success"} text={running ? "检测中" : "每分钟"} />
      </div>
      <div className="performance-tooltip-grid">
        {results.map((result) => (
          <div className="performance-tooltip-row" key={result.key}>
            <span>{result.title}</span>
            <strong className={isPrimaryDataLatency(result) ? `performance-tooltip-latency-${latencyTone(result)}` : undefined}>
              {formatLatencyValue(result.latencyMs)}ms
            </strong>
            {result.error ? <em>{result.error}</em> : <em>{result.meta}</em>}
          </div>
        ))}
      </div>
    </div>
  );
}

function waitForSocketOpen(socket: WebSocket, start: number): Promise<Partial<PerformanceMetricResult>> {
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "握手超时"));
    }, 8_000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("open", onOpen);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onOpen = () => {
      cleanup();
      resolve(metricOk(start, "已连接"));
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "握手失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "连接关闭"));
    };
    socket.addEventListener("open", onOpen);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
  });
}

function measureMarketPing(socket: WebSocket): Promise<Partial<PerformanceMetricResult>> {
  const requestId = `market-ping:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  const start = performance.now();
  socket.send(JSON.stringify({ type: "market.ping", request_id: requestId }));
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "Ping 超时"));
    }, 8_000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "Ping 失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "连接关闭"));
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parseMarketPong(event.data);
      if (!message || message.request_id !== requestId) return;
      cleanup();
      resolve(metricOk(start, "pong"));
    };
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
  });
}

function measureMarketCandles(socket: WebSocket): Promise<Partial<PerformanceMetricResult>> {
  const requestId = `market-candles:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  const payload: MarketCandlesRequest = {
    type: "market.candles.request",
    request_id: requestId,
    symbol: "BTCUSDT",
    interval: PERFORMANCE_INTERVAL,
    limit: 300,
  };
  const start = performance.now();
  socket.send(JSON.stringify(payload));
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "K线超时"));
    }, 8_000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "K线失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "连接关闭"));
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parseMarketWsMessage(event.data);
      if (!message || !("request_id" in message) || message.request_id !== requestId) return;
      cleanup();
      if (message.type === "market.candles.error") {
        resolve(metricError(start, message.message || "K线失败"));
        return;
      }
      resolve(metricOk(start, `${message.candles.length} 根`));
    };
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
  });
}

async function measurePolymarketOrderbook(interval: PolymarketInterval): Promise<Partial<PerformanceMetricResult>> {
  let socket: WebSocket | null = null;
  const connectStart = performance.now();
  try {
    socket = new WebSocket(api.polymarketBtcUpDownWsUrl(interval));
    const openResult = await waitForSocketOpen(socket, connectStart);
    if (openResult.status === "error") return metricError(connectStart, openResult.error || "盘口 WS 握手失败");
    const marketId = await waitForPolymarketInitialMarketId(socket);
    // 性能值只统计“已连接后主动订阅单个盘口 -> 对应 snapshot 回包”的耗时。
    return await measurePolymarketMarketSubscribe(socket, interval, marketId);
  } catch (error) {
    return metricError(connectStart, errorMessage(error));
  } finally {
    socket?.close();
  }
}

function waitForPolymarketInitialMarketId(socket: WebSocket): Promise<string> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      rejectWithMessage("盘口列表超时");
    }, 8_000);
    const rejectWithMessage = (message: string) => reject(new Error(message));
    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      rejectWithMessage("盘口 WS 失败");
    };
    const onClose = () => {
      cleanup();
      rejectWithMessage("盘口 WS 已关闭");
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parsePolymarketWsMessage(event.data);
      if (!message) return;
      if (message.type === "polymarket.btc_up_down.error") {
        cleanup();
        rejectWithMessage(message.message || "盘口列表失败");
        return;
      }
      if (message.type === "polymarket.btc_up_down.market.snapshot") {
        cleanup();
        resolve(message.market.id);
        return;
      }
      const market = message.markets.find((item) => item.id);
      if (!market) return;
      cleanup();
      resolve(market.id);
    };
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
  });
}

function measurePolymarketMarketSubscribe(
  socket: WebSocket,
  interval: PolymarketInterval,
  marketId: string,
): Promise<Partial<PerformanceMetricResult>> {
  const requestId = `polymarket-market:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  const start = performance.now();
  socket.send(
    JSON.stringify({
      type: "polymarket.btc_up_down.market.subscribe",
      interval,
      market_id: marketId,
      request_id: requestId,
    }),
  );
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "盘口订阅超时"));
    }, 8_000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "盘口订阅失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "连接关闭"));
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parsePolymarketWsMessage(event.data);
      if (!message || message.request_id !== requestId) return;
      cleanup();
      if (message.type === "polymarket.btc_up_down.error") {
        resolve(metricError(start, message.message || "盘口订阅失败"));
        return;
      }
      if (message.type === "polymarket.btc_up_down.markets.snapshot") {
        resolve(metricOk(start, `${message.markets.length} 市场 / ${countPolymarketQuotes(message.markets)} 报价`));
        return;
      }
      resolve(metricOk(start, `${message.market.outcome_quotes.length} 报价`));
    };
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
  });
}

function parseMarketPong(raw: string): { type: "market.pong"; request_id: string } | null {
  try {
    const payload = JSON.parse(raw) as { type?: string; request_id?: unknown };
    if (payload.type === "market.pong" && typeof payload.request_id === "string") return payload as { type: "market.pong"; request_id: string };
  } catch {
    return null;
  }
  return null;
}

function parseMarketWsMessage(raw: string): MarketWsMessage | null {
  try {
    const payload = JSON.parse(raw) as MarketWsMessage;
    if (payload?.type === "market.candles.snapshot" && Array.isArray(payload.candles)) return payload;
    if (payload?.type === "market.candles.error" && typeof payload.request_id === "string") return payload;
    if (payload?.type === "market.candle") return payload;
  } catch {
    return null;
  }
  return null;
}

function parsePolymarketWsMessage(raw: string): PolymarketWsMessage | null {
  try {
    const payload = JSON.parse(raw) as PolymarketWsMessage;
    if (payload?.type === "polymarket.btc_up_down.markets.snapshot" && Array.isArray(payload.markets)) return payload;
    if (payload?.type === "polymarket.btc_up_down.market.snapshot" && payload.market) return payload;
    if (payload?.type === "polymarket.btc_up_down.error") return payload;
  } catch {
    return null;
  }
  return null;
}

function metricOk(start: number, meta: string): Partial<PerformanceMetricResult> {
  return { latencyMs: performance.now() - start, status: "ok", meta, error: "" };
}

function metricError(start: number, error: string): Partial<PerformanceMetricResult> {
  return { latencyMs: performance.now() - start, status: "error", meta: "", error };
}

function latencyTone(result?: PerformanceMetricResult): PerformanceLatencyTone {
  if (!result || result.status === "idle") return "default";
  if (result.status === "running") return "processing";
  if (result.status === "error") return "error";
  if (result.latencyMs === null) return "default";
  if (result.latencyMs < 300) return "success";
  if (result.latencyMs < 1000) return "warning";
  return "error";
}

function maxLatencyTone(results: PerformanceMetricResult[]): PerformanceLatencyTone {
  const rank: Record<PerformanceLatencyTone, number> = {
    default: 0,
    success: 1,
    processing: 2,
    warning: 3,
    error: 4,
  };
  return results
    .map((result) => latencyTone(result))
    .reduce<PerformanceLatencyTone>((max, tone) => (rank[tone] > rank[max] ? tone : max), "default");
}

function isPrimaryDataLatency(result: PerformanceMetricResult) {
  return result.key === "candles" || result.key === "polymarket";
}

function countPolymarketQuotes(markets: PolymarketUpDownMarket[]) {
  return markets.reduce((sum, market) => sum + market.outcome_quotes.length, 0);
}

function formatLatencyValue(value: number | null) {
  return value === null ? "-" : value.toFixed(1);
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "未知错误";
}
