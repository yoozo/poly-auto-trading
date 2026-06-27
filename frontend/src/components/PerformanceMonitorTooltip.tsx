import { Badge, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type MarketCandlesRequest,
  type MarketWsMessage,
  type PolymarketInterval,
  type PolymarketUpDownMarket,
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

const PERFORMANCE_INTERVAL: PolymarketInterval = "5m";
const DETECT_INTERVAL_MS = 60_000;
export const PERFORMANCE_MONITOR_ENABLED_KEY = "poly-auto.performanceMonitorEnabled";
export const PERFORMANCE_MONITOR_ENABLED_EVENT = "poly-auto.performanceMonitorEnabled.changed";

const DEFAULT_RESULTS: PerformanceMetricResult[] = [
  { key: "ws_handshake", title: "WS 握手", latencyMs: null, status: "idle", meta: "", error: "" },
  { key: "ws_ping", title: "Ping", latencyMs: null, status: "idle", meta: "", error: "" },
  { key: "candles", title: "K线", latencyMs: null, status: "idle", meta: "BTCUSDT 5m", error: "" },
  { key: "polymarket", title: "盘口", latencyMs: null, status: "idle", meta: "Polymarket 5m", error: "" },
];

export function PerformanceMonitorTooltip() {
  const [results, setResults] = useState<PerformanceMetricResult[]>(DEFAULT_RESULTS);
  const [running, setRunning] = useState(false);
  const [enabled, setEnabled] = useState(() => readPerformanceMonitorEnabled());
  const runningRef = useRef(false);
  const mountedRef = useRef(true);

  const status = useMemo(() => {
    if (running) return "processing";
    if (results.some((result) => result.status === "error")) return "error";
    if (results.every((result) => result.status === "ok")) return "success";
    return "default";
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
    <Tooltip placement="bottomLeft" mouseEnterDelay={0.2} title={<PerformanceTooltipContent results={results} running={running} />}>
      <button className={`performance-tooltip-trigger performance-tooltip-trigger-${status}`} type="button" aria-label="性能检测结果">
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
            <strong>{formatLatencyValue(result.latencyMs)}ms</strong>
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
  const start = performance.now();
  try {
    const markets = await api.polymarketBtcUpDown(interval, 12);
    return metricOk(start, `${markets.length} 市场 / ${countPolymarketQuotes(markets)} 报价`);
  } catch (error) {
    return metricError(start, errorMessage(error));
  }
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

function metricOk(start: number, meta: string): Partial<PerformanceMetricResult> {
  return { latencyMs: performance.now() - start, status: "ok", meta, error: "" };
}

function metricError(start: number, error: string): Partial<PerformanceMetricResult> {
  return { latencyMs: performance.now() - start, status: "error", meta: "", error };
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
