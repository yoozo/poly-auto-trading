import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { Badge, Button, Card, Col, Empty, Row, Space, Statistic, Switch, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type MarketCandlesRequest,
  type MarketWsMessage,
  type PolymarketInterval,
  type PolymarketUpDownMarket,
  type ServiceEventRecord,
  type ServiceHealth
} from "../api/client";
import {
  PERFORMANCE_MONITOR_ENABLED_EVENT,
  readPerformanceMonitorEnabled,
  setPerformanceMonitorEnabled,
} from "../components/PerformanceMonitorTooltip";

const stateColor: Record<string, "success" | "processing" | "default" | "error" | "warning"> = {
  running: "success",
  idle: "default",
  unknown: "warning",
  error: "error"
};

export default function SystemStatusPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 10_000 });
  const services = useQuery({
    queryKey: ["services"],
    queryFn: api.services,
    refetchInterval: 10_000
  });
  const events = useQuery({
    queryKey: ["service-events"],
    queryFn: () => api.serviceEvents({ limit: 80 }),
    refetchInterval: 10_000
  });

  return (
    <div className="page-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="API" value={health.data?.checks.api.ok ? "OK" : "Unknown"} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic
              title="Database"
              value={health.data?.checks.database.ok ? "OK" : "Unavailable"}
              valueStyle={{ color: health.data?.checks.database.ok ? "#16a34a" : "#dc2626" }}
            />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="Updated" value={health.data ? new Date(health.data.time).toLocaleTimeString() : "-"} />
          </Card>
        </Col>
      </Row>

      <Card title="服务状态">
        <Table<ServiceHealth>
          rowKey="name"
          loading={services.isFetching}
          dataSource={services.data ?? []}
          pagination={false}
          columns={[
            {
              title: "服务",
              dataIndex: "name"
            },
            {
              title: "状态",
              dataIndex: "state",
              render: (value: string) => <Badge status={stateColor[value] ?? "default"} text={value} />
            },
            {
              title: "更新时间",
              dataIndex: "last_update",
              render: (value: string) => new Date(value).toLocaleString()
            },
            {
              title: "错误",
              dataIndex: "last_error",
              render: (value: string | null) => value || <Typography.Text type="secondary">-</Typography.Text>
            },
            {
              title: "详情",
              dataIndex: "metadata",
              render: (_value: unknown, record) => renderServiceMetadata(record)
            }
          ]}
        />
      </Card>

      <PerformanceTestCard />

      <Card
        title="服务事件"
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={() => events.refetch()} loading={events.isFetching}>
            刷新
          </Button>
        }
      >
        <Table<ServiceEventRecord>
          rowKey="id"
          size="small"
          loading={events.isFetching}
          dataSource={events.data ?? []}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务事件" /> }}
          pagination={{ pageSize: 8, size: "small" }}
          columns={[
            {
              title: "时间",
              dataIndex: "created_at",
              width: 180,
              render: (value: string) => new Date(value).toLocaleString()
            },
            {
              title: "服务",
              dataIndex: "service",
              width: 140,
              render: (value: string) => <Tag>{value}</Tag>
            },
            {
              title: "级别",
              dataIndex: "level",
              width: 100,
              render: (value: string) => <Tag color={eventLevelColor(value)}>{value}</Tag>
            },
            {
              title: "消息",
              dataIndex: "message"
            },
            {
              title: "详情",
              dataIndex: "payload",
              render: (value: Record<string, unknown>) => (
                <Typography.Text code>{Object.keys(value).length ? JSON.stringify(value) : "-"}</Typography.Text>
              )
            }
          ]}
        />
      </Card>

      <Card title="权限 / 配置预留">
        <Space wrap size={8}>
          <Tag color="default">RBAC 未启用</Tag>
          <Tag color="default">策略配置未启用</Tag>
          <Typography.Text type="secondary">当前版本仅预留入口，后续可接入用户、角色和系统级参数。</Typography.Text>
        </Space>
      </Card>
    </div>
  );
}

type PerformanceMetricKey = "ws_handshake" | "ws_ping" | "candles" | "polymarket";

type PerformanceMetricResult = {
  key: PerformanceMetricKey;
  title: string;
  description: string;
  latencyMs: number | null;
  status: "idle" | "running" | "ok" | "error";
  meta: string;
  error: string;
};

const PERFORMANCE_INTERVAL: PolymarketInterval = "5m";

const DEFAULT_PERFORMANCE_RESULTS: PerformanceMetricResult[] = [
  {
    key: "ws_handshake",
    title: "客户到服务器的 WS 握手时间",
    description: "/api/ws/market 建连到 open",
    latencyMs: null,
    status: "idle",
    meta: "BTC Watch K 线 WS",
    error: ""
  },
  {
    key: "ws_ping",
    title: "WS ping 回包延迟",
    description: "market.ping 到 market.pong 的 RTT",
    latencyMs: null,
    status: "idle",
    meta: "应用层 ping/pong",
    error: ""
  },
  {
    key: "candles",
    title: "获取 K 线的时间",
    description: "market.candles.request 到 snapshot",
    latencyMs: null,
    status: "idle",
    meta: "BTCUSDT / 5m / 300 根",
    error: ""
  },
  {
    key: "polymarket",
    title: "获取 Polymarket 盘口数据的时间",
    description: "/api/polymarket/btc-up-down 初始快照",
    latencyMs: null,
    status: "idle",
    meta: "12 个市场",
    error: ""
  }
];

function PerformanceTestCard() {
  const [results, setResults] = useState<PerformanceMetricResult[]>(DEFAULT_PERFORMANCE_RESULTS);
  const [running, setRunning] = useState(false);
  const [realtimeEnabled, setRealtimeEnabledState] = useState(() => readPerformanceMonitorEnabled());
  const runIdRef = useRef(0);
  const runningRef = useRef(false);
  const successCount = useMemo(() => results.filter((result) => result.status === "ok").length, [results]);

  const updateResult = useCallback((key: PerformanceMetricKey, patch: Partial<PerformanceMetricResult>) => {
    setResults((current) => current.map((result) => (result.key === key ? { ...result, ...patch } : result)));
  }, []);

  const runTest = useCallback(async () => {
    if (runningRef.current) return;
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    runningRef.current = true;
    setRunning(true);
    setResults(DEFAULT_PERFORMANCE_RESULTS.map((result) => ({ ...result, status: "running", latencyMs: null, error: "" })));
    let marketSocket: WebSocket | null = null;
    try {
      const handshakeStart = performance.now();
      marketSocket = new WebSocket(api.marketWsUrl(PERFORMANCE_INTERVAL));
      const handshakeResult = await waitForMarketSocketOpen(marketSocket, handshakeStart);
      if (runIdRef.current !== runId) return;
      updateResult("ws_handshake", handshakeResult);
      if (handshakeResult.status === "error") return;

      const pingResult = await measureMarketPing(marketSocket);
      if (runIdRef.current !== runId) return;
      updateResult("ws_ping", pingResult);
      if (pingResult.status === "error") return;

      const candlesResult = await measureMarketCandles(marketSocket);
      if (runIdRef.current !== runId) return;
      updateResult("candles", candlesResult);

      const polymarketResult = await measurePolymarketOrderbook(PERFORMANCE_INTERVAL);
      if (runIdRef.current !== runId) return;
      updateResult("polymarket", polymarketResult);
    } finally {
      marketSocket?.close();
      if (runIdRef.current === runId) {
        runningRef.current = false;
        setRunning(false);
      }
    }
  }, [updateResult]);

  useEffect(() => {
    const syncEnabled = () => setRealtimeEnabledState(readPerformanceMonitorEnabled());
    window.addEventListener("storage", syncEnabled);
    window.addEventListener(PERFORMANCE_MONITOR_ENABLED_EVENT, syncEnabled);
    return () => {
      window.removeEventListener("storage", syncEnabled);
      window.removeEventListener(PERFORMANCE_MONITOR_ENABLED_EVENT, syncEnabled);
    };
  }, []);

  const handleRealtimeChange = (checked: boolean) => {
    setRealtimeEnabledState(checked);
    setPerformanceMonitorEnabled(checked);
  };

  return (
    <Card
      title="接口性能检测"
      extra={
        <Space wrap>
          <Space size={6}>
            <Typography.Text type="secondary">实时检测</Typography.Text>
            <Switch size="small" checked={realtimeEnabled} onChange={handleRealtimeChange} />
          </Space>
          <Button size="small" type="primary" icon={<ThunderboltOutlined />} onClick={runTest} loading={running}>
            开始检测
          </Button>
        </Space>
      }
    >
      <Row gutter={[16, 16]}>
        {results.map((result) => (
          <Col xs={24} md={12} xl={6} key={result.key}>
            <div className={`performance-metric performance-metric-${result.status}`}>
              <div className="performance-metric-head">
                <span>{result.title}</span>
                <Badge status={metricStatusColor(result.status)} text={metricStatusText(result.status)} />
              </div>
              <Statistic value={formatLatencyValue(result.latencyMs)} suffix="ms" />
              <Typography.Text type="secondary">{result.description}</Typography.Text>
              <div className="performance-metric-meta">
                {result.error ? (
                  <Typography.Text type="danger">{result.error}</Typography.Text>
                ) : (
                  <Typography.Text type="secondary">{result.meta}</Typography.Text>
                )}
              </div>
            </div>
          </Col>
        ))}
      </Row>
      <Typography.Paragraph className="performance-metric-note" type="secondary">
        已完成 {successCount}/4。所有检测固定使用 5m；开启实时检测后所有页面显示低调 tooltip，并每 1 分钟自动检测一次。
      </Typography.Paragraph>
    </Card>
  );
}

function waitForMarketSocketOpen(socket: WebSocket, start: number): Promise<Partial<PerformanceMetricResult>> {
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "WebSocket 握手超时"));
    }, 8_000);

    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("open", onOpen);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onOpen = () => {
      cleanup();
      resolve(metricOk(start, "连接已打开"));
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "WebSocket 握手失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "WebSocket 握手前已关闭"));
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
      resolve(metricError(start, "WebSocket ping 回包超时"));
    }, 8_000);

    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "WebSocket ping 失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "WebSocket 已关闭"));
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parseMarketPongMessage(event.data);
      if (!message || message.request_id !== requestId) return;
      cleanup();
      resolve(metricOk(start, "market.pong 已返回"));
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
    limit: 300
  };
  const start = performance.now();
  socket.send(JSON.stringify(payload));
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve(metricError(start, "K 线快照等待超时"));
    }, 8_000);

    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
    };
    const onError = () => {
      cleanup();
      resolve(metricError(start, "K 线请求失败"));
    };
    const onClose = () => {
      cleanup();
      resolve(metricError(start, "WebSocket 已关闭"));
    };
    const onMessage = (event: MessageEvent<string>) => {
      const message = parseMarketWsMessage(event.data);
      if (!message || !("request_id" in message) || message.request_id !== requestId) return;
      cleanup();
      if (message.type === "market.candles.error") {
        resolve(metricError(start, message.message || "K 线请求失败"));
        return;
      }
      resolve(metricOk(start, `${message.candles.length} 根 K 线`));
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
    return metricOk(start, `${markets.length} 个市场 / ${countPolymarketQuotes(markets)} 个报价`);
  } catch (error) {
    return metricError(start, errorMessage(error));
  }
}

type MarketPongMessage = {
  type: "market.pong";
  request_id: string;
};

function parseMarketPongMessage(raw: string): MarketPongMessage | null {
  try {
    const payload = JSON.parse(raw) as MarketPongMessage;
    if (payload?.type === "market.pong" && typeof payload.request_id === "string") return payload;
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
  return {
    latencyMs: performance.now() - start,
    status: "ok",
    meta,
    error: ""
  };
}

function metricError(start: number, error: string): Partial<PerformanceMetricResult> {
  return {
    latencyMs: performance.now() - start,
    status: "error",
    error,
    meta: ""
  };
}

function countPolymarketQuotes(markets: PolymarketUpDownMarket[]) {
  return markets.reduce((sum, market) => sum + market.outcome_quotes.length, 0);
}

function metricStatusColor(status: PerformanceMetricResult["status"]) {
  if (status === "ok") return "success";
  if (status === "error") return "error";
  if (status === "running") return "processing";
  return "default";
}

function metricStatusText(status: PerformanceMetricResult["status"]) {
  if (status === "ok") return "OK";
  if (status === "error") return "失败";
  if (status === "running") return "检测中";
  return "未检测";
}

function compactMetricLabel(key: PerformanceMetricKey) {
  if (key === "ws_handshake") return "WS";
  if (key === "ws_ping") return "Ping";
  if (key === "candles") return "K线";
  return "盘口";
}

function formatLatencyValue(value: number | null) {
  return value === null ? "-" : value.toFixed(1);
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "未知错误";
}

function renderServiceMetadata(record: ServiceHealth) {
  if (record.name === "kline_backfill" || record.name === "indicator_backfill") {
    const metadata = record.metadata as { task_id?: number; symbol?: string; current_interval?: string; state?: string };
    return (
      <Typography.Text>
        #{metadata.task_id || "-"} / {metadata.symbol || "BTCUSDT"} / {metadata.current_interval || metadata.state || record.state}
      </Typography.Text>
    );
  }
  if (record.name !== "telegram") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  const configured = Boolean(record.metadata?.configured);
  const enabled = Boolean(record.metadata?.enabled);
  const lastDelivery = record.metadata?.last_delivery as { title?: string; status?: string } | undefined;
  return (
    <Typography.Text>
      {configured ? "已配置" : "未配置"} / {enabled ? "已开启" : "已关闭"}
      {lastDelivery?.title ? ` / ${lastDelivery.title}: ${lastDelivery.status || "-"}` : ""}
    </Typography.Text>
  );
}

function eventLevelColor(level: string) {
  if (level === "error") return "error";
  if (level === "warning") return "warning";
  if (level === "info") return "processing";
  return "default";
}
