import { FullscreenExitOutlined, FullscreenOutlined } from "@ant-design/icons";
import { Button, Card, Segmented, Space, Switch, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { api, type CandleInterval } from "../api/client";
import BtcWatchChart from "../components/market-chart/BtcWatchChart";
import type { MarketCandle, MarketIndicatorPoint, StreamStatus } from "../components/market-chart/types";
import { mergeCandles } from "../components/market-chart/utils";

const intervals: CandleInterval[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const INTERVAL_KEY = "poly-auto.btcWatch.interval";
const BOLL_KEY = "poly-auto.btcWatch.boll";
const RSI_KEY = "poly-auto.btcWatch.rsi";

export default function BTCWatchPage() {
  const queryClient = useQueryClient();
  const [interval, setInterval] = useState<CandleInterval>(() => {
    const saved = localStorage.getItem(INTERVAL_KEY);
    return intervals.includes(saved as CandleInterval) ? (saved as CandleInterval) : "1m";
  });
  const [showBollinger, setShowBollinger] = useState(() => localStorage.getItem(BOLL_KEY) !== "0");
  const [showRsi, setShowRsi] = useState(() => localStorage.getItem(RSI_KEY) !== "0");
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [candles, setCandles] = useState<MarketCandle[]>([]);
  const [indicatorPoints, setIndicatorPoints] = useState<MarketIndicatorPoint[]>([]);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fitAnchorVersion, setFitAnchorVersion] = useState(0);
  const indicatorLimit = Math.min(Math.max(candles.length, 300), 1000);

  const { data: latestCandles = [], error } = useQuery({
    queryKey: ["candles", interval],
    queryFn: () => api.candles(interval, 300),
  });
  const { data: latestIndicators = [] } = useQuery({
    queryKey: ["indicators", interval, indicatorLimit],
    queryFn: () => api.indicators(interval, indicatorLimit),
  });

  const activeCandles = candles.filter((candle) => candle.interval === interval);
  const activeIndicators = indicatorPoints.filter((point) => point.interval === interval);
  const latest = activeCandles.at(-1);

  useEffect(() => {
    localStorage.setItem(INTERVAL_KEY, interval);
    setCandles([]);
    setIndicatorPoints([]);
  }, [interval]);

  useEffect(() => {
    localStorage.setItem(BOLL_KEY, showBollinger ? "1" : "0");
  }, [showBollinger]);

  useEffect(() => {
    localStorage.setItem(RSI_KEY, showRsi ? "1" : "0");
  }, [showRsi]);

  useEffect(() => {
    setCandles((current) => mergeCandles(current, latestCandles));
  }, [latestCandles]);

  useEffect(() => {
    setIndicatorPoints((current) => mergeIndicators(current, latestIndicators as MarketIndicatorPoint[]));
  }, [latestIndicators]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer = 0;
    let closedByEffect = false;

    const connect = () => {
      setStreamStatus("connecting");
      socket = new WebSocket(api.marketWsUrl(interval));
      socket.onopen = () => setStreamStatus("connected");
      socket.onmessage = (event) => {
        setStreamStatus("connected");
        const message = parseMarketMessage(event.data);
        if (!message || message.symbol !== "BTCUSDT" || message.interval !== interval) return;
        const candle = message.candle;
        const indicator = message.indicator;
        if (candle) {
          setCandles((current) => mergeCandles(current, [candle]));
        }
        if (indicator) {
          setIndicatorPoints((current) => mergeIndicators(current, [indicator]));
        }
      };
      socket.onerror = () => setStreamStatus("reconnecting");
      socket.onclose = () => {
        if (closedByEffect) {
          setStreamStatus("closed");
          return;
        }
        setStreamStatus("reconnecting");
        reconnectTimer = window.setTimeout(connect, 1000);
      };
    };

    connect();
    return () => {
      closedByEffect = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [interval]);

  const loadMore = useCallback(
    async (startMs: number, endMs: number) => {
      setIsLoadingMore(true);
      try {
        const older = await api.candlesRange(interval, startMs, endMs);
        setCandles((current) => mergeCandles(current, older));
        const olderIndicators = await queryClient.fetchQuery({
          queryKey: ["indicators-range", interval, startMs, endMs],
          queryFn: () => api.indicatorsRange(interval, startMs, endMs),
        });
        setIndicatorPoints((current) => mergeIndicators(current, olderIndicators as MarketIndicatorPoint[]));
      } finally {
        setIsLoadingMore(false);
      }
    },
    [interval, queryClient]
  );

  const toggleFullscreen = useCallback(() => {
    setIsFullscreen((value) => {
      const nextValue = !value;
      if (nextValue) setFitAnchorVersion((version) => version + 1);
      return nextValue;
    });
  }, []);

  return (
    <div className={isFullscreen ? "watch-page watch-page-fullscreen" : "watch-page"}>
      <Card className="watch-toolbar" styles={{ body: { padding: 8 } }}>
        <div className="watch-toolbar-inner">
          <div className="watch-toolbar-controls">
            <Typography.Text strong>BTCUSDT</Typography.Text>
            <Segmented
              size="small"
              options={intervals}
              value={interval}
              onChange={(value) => setInterval(value as CandleInterval)}
            />
            <Button
              size="small"
              icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
              onClick={toggleFullscreen}
            >
              {isFullscreen ? "退出全屏" : "全屏"}
            </Button>
            <Space size={6}>
              <Typography.Text type="secondary">BOLL</Typography.Text>
              <Switch size="small" checked={showBollinger} onChange={setShowBollinger} />
            </Space>
            <Space size={6}>
              <Typography.Text type="secondary">RSI</Typography.Text>
              <Switch size="small" checked={showRsi} onChange={setShowRsi} />
            </Space>
          </div>
          <div className="watch-toolbar-status">
            {latest && (
              <Typography.Text type="secondary">
                最新 {latest.close.toLocaleString("en-US", { maximumFractionDigits: 2 })}
              </Typography.Text>
            )}
            <Typography.Text type={streamStatus === "connected" ? "success" : "warning"}>
              实时流 {streamStatusLabel(streamStatus)}
            </Typography.Text>
            {isLoadingMore && <Typography.Text type="secondary">加载历史中...</Typography.Text>}
            {error instanceof Error && <Typography.Text type="danger">{error.message}</Typography.Text>}
          </div>
        </div>
      </Card>
      <Card className="watch-chart-card btc-watch-card" styles={{ body: { padding: 0 } }}>
        <BtcWatchChart
          key={`BTCUSDT-${interval}`}
          symbol="BTCUSDT"
          interval={interval}
          candles={activeCandles}
          indicators={activeIndicators}
          showBollinger={showBollinger}
          showRsi={showRsi}
          onLoadMore={loadMore}
          isLoadingMore={isLoadingMore}
          latestStreamStatus={streamStatus}
          fitAnchorVersion={fitAnchorVersion}
        />
      </Card>
    </div>
  );
}

type MarketWsMessage = {
  type: "market.candle";
  symbol: string;
  interval: CandleInterval;
  candle: MarketCandle | null;
  indicator: MarketIndicatorPoint | null;
};

function parseMarketMessage(value: string) {
  try {
    const message = JSON.parse(value) as MarketWsMessage;
    if (message.type !== "market.candle") return null;
    return message;
  } catch {
    return null;
  }
}

function mergeIndicators(existing: MarketIndicatorPoint[], incoming: MarketIndicatorPoint[]) {
  const byKey = new Map<string, MarketIndicatorPoint>();
  for (const point of existing) {
    byKey.set(`${point.symbol}:${point.interval}:${point.candle_time}`, point);
  }
  for (const point of incoming) {
    byKey.set(`${point.symbol}:${point.interval}:${point.candle_time}`, point);
  }
  return Array.from(byKey.values()).sort(
    (left, right) => new Date(left.candle_time).getTime() - new Date(right.candle_time).getTime()
  );
}

function streamStatusLabel(status: StreamStatus) {
  if (status === "connected") return "已连接";
  if (status === "reconnecting") return "重连中";
  if (status === "closed") return "已关闭";
  return "连接中";
}
