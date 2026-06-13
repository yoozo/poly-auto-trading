import { FullscreenExitOutlined, FullscreenOutlined } from "@ant-design/icons";
import { Button, Card, Checkbox, Empty, Segmented, Select, Space, Switch, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CandleInterval,
  type PolymarketInterval,
  type PolymarketOutcomeQuote,
  type PolymarketUpDownMarket,
  type PolymarketWsMessage,
} from "../api/client";
import BtcWatchChart from "../components/market-chart/BtcWatchChart";
import type {
  ChartComparisonLine,
  MarketCandle,
  MarketIndicatorPoint,
  StreamStatus,
} from "../components/market-chart/types";
import { mergeCandles } from "../components/market-chart/utils";

const intervals: CandleInterval[] = ["1m", "5m", "15m", "1h", "4h"];
const polymarketIntervals: PolymarketInterval[] = ["5m", "15m", "1h", "4h"];
const INTERVAL_KEY = "poly-auto.btcWatch.interval";
const BOLL_KEY = "poly-auto.btcWatch.boll";
const RSI_KEY = "poly-auto.btcWatch.rsi";
const POLY_INTERVAL_KEY = "poly-auto.btcWatch.polymarketInterval";

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
  const [polymarketInterval, setPolymarketInterval] = useState<PolymarketInterval>(() => {
    const saved = localStorage.getItem(POLY_INTERVAL_KEY);
    return polymarketIntervals.includes(saved as PolymarketInterval) ? (saved as PolymarketInterval) : "5m";
  });
  const [selectedPolymarketId, setSelectedPolymarketId] = useState<string | null>(null);
  const [autoSwitchPolymarket, setAutoSwitchPolymarket] = useState(true);
  const [polymarketMarkets, setPolymarketMarkets] = useState<PolymarketUpDownMarket[]>([]);
  const [comparisonLine, setComparisonLine] = useState<ChartComparisonLine | null>(null);
  const comparisonRequestKeyRef = useRef<string | null>(null);
  const activeComparisonKeyRef = useRef<string | null>(null);
  const comparisonLineCacheRef = useRef<Map<string, ChartComparisonLine>>(new Map());
  // 指标计算需要足够 warmup 数据，按当前 K 线数量动态扩大查询窗口。
  const indicatorLimit = Math.min(Math.max(candles.length, 300), 1000);

  const { data: latestCandles = [], error } = useQuery({
    queryKey: ["candles", interval],
    queryFn: () => api.candles(interval, 300),
  });
  const { data: latestIndicators = [] } = useQuery({
    queryKey: ["indicators", interval, indicatorLimit],
    queryFn: () => api.indicators(interval, indicatorLimit),
  });
  const { data: polymarketSnapshot = [], error: polymarketError } = useQuery({
    queryKey: ["polymarket-btc-up-down", polymarketInterval],
    queryFn: () => api.polymarketBtcUpDown(polymarketInterval, 12),
    refetchInterval: 5_000,
  });

  const activeCandles = candles.filter((candle) => candle.interval === interval);
  const activeIndicators = indicatorPoints.filter((point) => point.interval === interval);
  const latest = activeCandles.at(-1);
  const selectedPolymarket =
    polymarketMarkets.find((market) => market.id === selectedPolymarketId) ??
    polymarketMarkets.find((market) => market.window === "current") ??
    polymarketMarkets.find((market) => market.window === "next") ??
    polymarketMarkets[0];

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
    localStorage.setItem(POLY_INTERVAL_KEY, polymarketInterval);
    setSelectedPolymarketId(null);
    setAutoSwitchPolymarket(true);
    setPolymarketMarkets([]);
    setComparisonLine(null);
    comparisonRequestKeyRef.current = null;
    activeComparisonKeyRef.current = null;
    comparisonLineCacheRef.current.clear();
  }, [polymarketInterval]);

  useEffect(() => {
    setPolymarketMarkets(polymarketSnapshot);
  }, [polymarketSnapshot]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer = 0;
    let closedByEffect = false;

    const connect = () => {
      socket = new WebSocket(api.polymarketBtcUpDownWsUrl(polymarketInterval));
      socket.onmessage = (event) => {
        const message = parsePolymarketMessage(event.data);
        if (!message || message.interval !== polymarketInterval) return;
        setPolymarketMarkets(message.markets);
      };
      socket.onclose = () => {
        if (closedByEffect) return;
        reconnectTimer = window.setTimeout(connect, 1000);
      };
    };

    connect();
    return () => {
      closedByEffect = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [polymarketInterval]);

  useEffect(() => {
    if (polymarketMarkets.length === 0) return;
    const selected = selectedPolymarketId
      ? polymarketMarkets.find((market) => market.id === selectedPolymarketId)
      : null;
    if (selected && (!autoSwitchPolymarket || selected.window !== "expired")) return;
    if (selected && selected.window === "expired" && !autoSwitchPolymarket) return;
    const nextMarket =
      polymarketMarkets.find((market) => market.window === "current") ??
      polymarketMarkets.find((market) => market.window === "next") ??
      polymarketMarkets.find((market) => market.window === "upcoming") ??
      polymarketMarkets[0];
    if (nextMarket && nextMarket.id !== selectedPolymarketId) {
      setSelectedPolymarketId(nextMarket.id);
    }
  }, [autoSwitchPolymarket, polymarketMarkets, selectedPolymarketId]);

  useEffect(() => {
    let cancelled = false;
    const marketId = selectedPolymarket?.id;
    const startTime = selectedPolymarket?.start_time;
    const marketKey = marketId && startTime ? `${marketId}:${startTime}` : null;
    const comparisonKey = marketKey && selectedPolymarket?.window === "current" ? marketKey : null;
    activeComparisonKeyRef.current = comparisonKey;

    if (!comparisonKey || !marketId || !startTime) {
      setComparisonLine(null);
      return () => {
        cancelled = true;
      };
    }
    const cachedLine = comparisonLineCacheRef.current.get(comparisonKey);
    if (cachedLine) {
      setComparisonLine(cachedLine);
      return () => {
        cancelled = true;
      };
    }
    if (comparisonRequestKeyRef.current === comparisonKey) {
      return () => {
        cancelled = true;
      };
    }
    comparisonRequestKeyRef.current = comparisonKey;
    setComparisonLine(null);

    const startMs = new Date(startTime).getTime();
    if (!Number.isFinite(startMs)) {
      return () => {
        cancelled = true;
      };
    }

    // Polymarket 当前窗口的比较基准取窗口起始 1m K 的 open，避免用概率价画到 BTC 价格轴。
    api
      .candlesRange("1m", startMs, startMs + 60_000, 2)
      .then((rows) => {
        if (cancelled || activeComparisonKeyRef.current !== comparisonKey) return;
        const candle =
          rows.find((row) => Math.abs(new Date(row.open_time).getTime() - startMs) < 1000) ??
          rows.find((row) => {
            const openMs = new Date(row.open_time).getTime();
            return openMs >= startMs && openMs < startMs + 60_000;
          });
        if (!candle || !Number.isFinite(candle.open)) {
          setComparisonLine(null);
          return;
        }
        const nextLine = {
          id: `polymarket:${marketId}:${startTime}`,
          price: candle.open,
          title: `比较 ${formatBtcPrice(candle.open)}`,
          color: "#f59e0b",
        };
        comparisonLineCacheRef.current.set(comparisonKey, nextLine);
        setComparisonLine(nextLine);
      })
      .catch(() => {
        if (!cancelled && activeComparisonKeyRef.current === comparisonKey) setComparisonLine(null);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedPolymarket?.id, selectedPolymarket?.start_time, selectedPolymarket?.window]);

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
      // WebSocket 只负责实时增量；初始窗口和向前翻页仍由 REST 接口补齐。
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
        // 历史翻页必须同步补 candle 和 indicator，否则图表时间轴会有价格但缺少指标层。
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
          comparisonLine={comparisonLine}
        />
      </Card>
      {!isFullscreen && (
        <PolymarketBtcPanel
          interval={polymarketInterval}
          onIntervalChange={setPolymarketInterval}
          markets={polymarketMarkets}
          selectedMarket={selectedPolymarket}
          selectedMarketId={selectedPolymarketId}
          onSelectedMarketId={(marketId) => {
            setSelectedPolymarketId(marketId);
            setAutoSwitchPolymarket(false);
          }}
          autoSwitch={autoSwitchPolymarket}
          onAutoSwitchChange={setAutoSwitchPolymarket}
          error={polymarketError instanceof Error ? polymarketError.message : null}
        />
      )}
    </div>
  );
}

function PolymarketBtcPanel({
  interval,
  onIntervalChange,
  markets,
  selectedMarket,
  selectedMarketId,
  onSelectedMarketId,
  autoSwitch,
  onAutoSwitchChange,
  error,
}: {
  interval: PolymarketInterval;
  onIntervalChange: (interval: PolymarketInterval) => void;
  markets: PolymarketUpDownMarket[];
  selectedMarket: PolymarketUpDownMarket | undefined;
  selectedMarketId: string | null;
  onSelectedMarketId: (marketId: string) => void;
  autoSwitch: boolean;
  onAutoSwitchChange: (checked: boolean) => void;
  error: string | null;
}) {
  const activeMarket =
    markets.find((market) => market.id === selectedMarketId) ??
    selectedMarket ??
    markets.find((market) => market.window === "current") ??
    markets.find((market) => market.window === "next") ??
    markets[0];
  const options = useMemo(
    () =>
      markets.map((market) => ({
        value: market.id,
        label: `${formatMarketTime(market)} · ${marketWindowLabel(market)}`,
      })),
    [markets]
  );

  return (
    <Card className="polymarket-panel" styles={{ body: { padding: 12 } }}>
      <div className="polymarket-panel-head">
        <Space size={10} wrap>
          <Typography.Text strong>Polymarket BTC Up/Down</Typography.Text>
          <Segmented
            size="small"
            value={interval}
            options={polymarketIntervals}
            onChange={(value) => onIntervalChange(value as PolymarketInterval)}
          />
        </Space>
        {activeMarket?.slug && (
          <Button
            size="small"
            type="link"
            href={`https://polymarket.com/event/${activeMarket.slug}`}
            target="_blank"
          >
            打开市场
          </Button>
        )}
      </div>
      <div className="polymarket-market-selector">
        <Select
          size="small"
          value={activeMarket?.id ?? selectedMarketId ?? undefined}
          placeholder="选择市场"
          options={options}
          onChange={onSelectedMarketId}
        />
        <Checkbox checked={autoSwitch} onChange={(event) => onAutoSwitchChange(event.target.checked)}>
          自动切换
        </Checkbox>
      </div>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
      {!activeMarket && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`暂无 ${interval} 市场`} />}
      {activeMarket && (
        <div className="polymarket-market">
          <div className="polymarket-market-meta">
            <Typography.Text strong>{activeMarket.title}</Typography.Text>
            <Typography.Text type="secondary">
              {formatMarketTime(activeMarket)} · {marketWindowLabel(activeMarket)} ·{" "}
              {activeMarket.accepting_orders ? "可交易" : "暂停接单"} · 流动性{" "}
              {formatCompact(activeMarket.liquidity)}
            </Typography.Text>
          </div>
          <div className="polymarket-outcomes">
            {activeMarket.outcome_quotes.map((quote) => (
              <OutcomeQuoteCard key={`${activeMarket.id}:${quote.name}`} quote={quote} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function OutcomeQuoteCard({ quote }: { quote: PolymarketOutcomeQuote }) {
  return (
    <div className={`polymarket-outcome ${quote.name.toLowerCase() === "up" ? "up" : "down"}`}>
      <div className="polymarket-outcome-title">
        <span>{quote.name}</span>
        <strong>{formatCents(quote.buy_price ?? quote.best_ask ?? quote.price)}</strong>
      </div>
      <div className="polymarket-quote-grid">
        <span>Sell</span>
        <strong>{formatCents(quote.sell_price ?? quote.best_bid)}</strong>
        <span>Buy</span>
        <strong>{formatCents(quote.buy_price ?? quote.best_ask)}</strong>
        <span>Last</span>
        <strong>{formatCents(quote.last_trade_price)}</strong>
      </div>
      <OrderBook quote={quote} />
    </div>
  );
}

function OrderBook({ quote }: { quote: PolymarketOutcomeQuote }) {
  const depth = Math.max(quote.bids.length, quote.asks.length);
  if (depth === 0) {
    return <Typography.Text type="secondary">暂无订单簿</Typography.Text>;
  }
  return (
    <div className="polymarket-orderbook">
      <div className="polymarket-orderbook-head">
        <span>Bid</span>
        <span>Size</span>
        <span>Ask</span>
        <span>Size</span>
      </div>
      {Array.from({ length: depth }).map((_, index) => {
        const bid = quote.bids[index];
        const ask = quote.asks[index];
        return (
          <div className="polymarket-orderbook-row" key={`${quote.token_id}:${index}`}>
            <span className="bid">{formatCents(bid?.price ?? null)}</span>
            <span>{formatSize(bid?.size ?? null)}</span>
            <span className="ask">{formatCents(ask?.price ?? null)}</span>
            <span>{formatSize(ask?.size ?? null)}</span>
          </div>
        );
      })}
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

function parsePolymarketMessage(value: string) {
  try {
    const message = JSON.parse(value) as PolymarketWsMessage;
    if (message.type !== "polymarket.btc_up_down.snapshot") return null;
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

function formatProbability(value: number | null) {
  if (value == null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatCents(value: number | null) {
  if (value == null) return "-";
  return `${Math.round(value * 100)}¢`;
}

function formatSize(value: number | null) {
  if (value == null) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatCompact(value: number | null) {
  if (value == null) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function formatBtcPrice(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatMarketTime(market: PolymarketUpDownMarket) {
  const start = market.start_time ? new Date(market.start_time) : null;
  const end = market.end_time ? new Date(market.end_time) : null;
  if (!start || !end) return market.window;
  const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${timeFormatter.format(start)}-${timeFormatter.format(end)}`;
}

function marketWindowLabel(market: PolymarketUpDownMarket) {
  if (market.window === "current") return "当前";
  if (market.window === "next") return "下个";
  if (market.window === "upcoming") return "未来";
  if (market.window === "expired") return "已结束";
  return "未知";
}
