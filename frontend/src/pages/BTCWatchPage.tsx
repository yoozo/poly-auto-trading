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
const ONE_MINUTE_MS = 60_000;
const POLYMARKET_INTERVAL_MS: Record<PolymarketInterval, number> = {
  "5m": 5 * ONE_MINUTE_MS,
  "15m": 15 * ONE_MINUTE_MS,
  "1h": 60 * ONE_MINUTE_MS,
  "4h": 4 * 60 * ONE_MINUTE_MS,
};
const ET_TIME_ZONE = "America/New_York";
const MONTH_INDEX: Record<string, number> = {
  january: 0,
  february: 1,
  march: 2,
  april: 3,
  may: 4,
  june: 5,
  july: 6,
  august: 7,
  september: 8,
  october: 9,
  november: 10,
  december: 11,
};

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
  const dataEpochRef = useRef(0);
  const activeIntervalRef = useRef<CandleInterval>(interval);
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
    // REST 只负责初始快照和切换 interval；后续盘口/窗口变化由 Polymarket WS 快照推送。
    staleTime: 5 * ONE_MINUTE_MS,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const activeCandles = useMemo(() => candles.filter((candle) => candle.interval === interval), [candles, interval]);
  const activeIndicators = useMemo(
    () => indicatorPoints.filter((point) => point.interval === interval),
    [indicatorPoints, interval]
  );
  const latest = activeCandles.at(-1);
  const selectedPolymarket =
    polymarketMarkets.find((market) => market.id === selectedPolymarketId) ??
    polymarketMarkets.find((market) => market.window === "current") ??
    polymarketMarkets.find((market) => market.window === "next") ??
    polymarketMarkets[0];
  const selectedPolymarketWindow = selectedPolymarket ? polymarketDisplayWindow(selectedPolymarket) : null;

  useEffect(() => {
    localStorage.setItem(INTERVAL_KEY, interval);
    activeIntervalRef.current = interval;
    dataEpochRef.current += 1;
    setCandles([]);
    setIndicatorPoints([]);
    setIsLoadingMore(false);
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
    let connectTimer = 0;
    let reconnectTimer = 0;
    let closedByEffect = false;

    const connect = () => {
      if (closedByEffect) return;
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

    // React StrictMode 开发态会立即 cleanup/re-run effect；延迟建连可避免 Vite proxy 出现一次性假连接。
    connectTimer = window.setTimeout(connect, 0);
    return () => {
      closedByEffect = true;
      if (connectTimer) window.clearTimeout(connectTimer);
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
    const marketWindow = selectedPolymarket ? polymarketDisplayWindow(selectedPolymarket) : null;
    const comparisonStartMs = marketWindow?.startMs ?? Number.NaN;
    const marketKey = marketId && Number.isFinite(comparisonStartMs) ? `${marketId}:${comparisonStartMs}` : null;
    const comparisonKey = marketKey && comparisonStartMs <= Date.now() ? marketKey : null;
    activeComparisonKeyRef.current = comparisonKey;

    if (!comparisonKey || !marketId || !Number.isFinite(comparisonStartMs)) {
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
    const visibleBaselineCandle = nearestBaselineCandle(activeCandles, comparisonStartMs);
    if (visibleBaselineCandle && Number.isFinite(visibleBaselineCandle.open)) {
      setComparisonLine(
        marketComparisonLine({
          marketId,
          startMs: comparisonStartMs,
          price: visibleBaselineCandle.open,
          interval: selectedPolymarket?.interval ?? "N/A",
        })
      );
    }
    if (comparisonRequestKeyRef.current === comparisonKey) {
      return () => {
        cancelled = true;
      };
    }
    comparisonRequestKeyRef.current = comparisonKey;
    setComparisonLine(null);

    // Polymarket 展示窗口可能和 API 的 eventStartTime 有几分钟偏移；基准线统一取派生窗口起点的 1m K open。
    void (async () => {
      try {
        const rows = await api.candlesRange(
          "1m",
          Math.max(0, comparisonStartMs),
          comparisonStartMs + 5 * ONE_MINUTE_MS,
          6
        );
        if (cancelled || activeComparisonKeyRef.current !== comparisonKey) return;
        const targetCandle = nearestBaselineCandle(rows, comparisonStartMs);
        if (!targetCandle || !Number.isFinite(targetCandle.open)) {
          if (!visibleBaselineCandle) setComparisonLine(null);
          return;
        }
        const nextLine = marketComparisonLine({
          marketId,
          startMs: comparisonStartMs,
          price: targetCandle.open,
          interval: selectedPolymarket?.interval ?? "N/A",
        });
        comparisonLineCacheRef.current.set(comparisonKey, nextLine);
        setComparisonLine(nextLine);
      } catch {
        if (!cancelled && activeComparisonKeyRef.current === comparisonKey && !visibleBaselineCandle) {
          setComparisonLine(null);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    activeCandles,
    selectedPolymarket?.id,
    selectedPolymarket?.interval,
    selectedPolymarket?.start_time,
    selectedPolymarket?.end_time,
  ]);

  useEffect(() => {
    const requestEpoch = dataEpochRef.current;
    setCandles((current) => {
      if (requestEpoch !== dataEpochRef.current) return current;
      return mergeCandles(current, latestCandles);
    });
  }, [latestCandles]);

  useEffect(() => {
    const requestEpoch = dataEpochRef.current;
    setIndicatorPoints((current) => {
      if (requestEpoch !== dataEpochRef.current) return current;
      return mergeIndicators(current, latestIndicators as MarketIndicatorPoint[]);
    });
  }, [latestIndicators]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let connectTimer = 0;
    let reconnectTimer = 0;
    let closedByEffect = false;
    const streamInterval = interval;
    const requestEpoch = dataEpochRef.current;

    const connect = () => {
      // WebSocket 只负责实时增量；初始窗口和向前翻页仍由 REST 接口补齐。
      if (requestEpoch !== dataEpochRef.current) return;
      setStreamStatus("connecting");
      socket = new WebSocket(api.marketWsUrl(streamInterval));
      socket.onopen = () => {
        if (requestEpoch !== dataEpochRef.current) return;
        setStreamStatus("connected");
      };
      socket.onmessage = (event) => {
        if (requestEpoch !== dataEpochRef.current || streamInterval !== activeIntervalRef.current) return;
        setStreamStatus("connected");
        const message = parseMarketMessage(event.data);
        if (!message || message.symbol !== "BTCUSDT" || message.interval !== streamInterval) return;
        const candle = message.candle;
        const indicator = message.indicator;
        if (candle) {
          setCandles((current) => mergeCandles(current, [candle]));
        }
        if (indicator) {
          setIndicatorPoints((current) => mergeIndicators(current, [indicator]));
        }
      };
      socket.onerror = () => {
        if (requestEpoch !== dataEpochRef.current || streamInterval !== activeIntervalRef.current) return;
        setStreamStatus("reconnecting");
      };
      socket.onclose = () => {
        if (closedByEffect) {
          setStreamStatus("closed");
          return;
        }
        if (requestEpoch !== dataEpochRef.current || streamInterval !== activeIntervalRef.current) return;
        setStreamStatus("reconnecting");
        reconnectTimer = window.setTimeout(connect, 1000);
      };
    };

    // React StrictMode 开发态会立即 cleanup/re-run effect；延迟建连可避免 Vite proxy 出现一次性假连接。
    connectTimer = window.setTimeout(connect, 0);
    return () => {
      closedByEffect = true;
      if (connectTimer) window.clearTimeout(connectTimer);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [interval]);

  const loadMore = useCallback(
    async (startMs: number, endMs: number) => {
      setIsLoadingMore(true);
      try {
        // 历史翻页必须同步补 candle 和 indicator，否则图表时间轴会有价格但缺少指标层。
        const requestEpoch = dataEpochRef.current;
        const older = await api.candlesRange(interval, startMs, endMs);
        if (requestEpoch !== dataEpochRef.current || interval !== activeIntervalRef.current) return;
        setCandles((current) => mergeCandles(current, older));
        const olderIndicators = await queryClient.fetchQuery({
          queryKey: ["indicators-range", interval, startMs, endMs],
          queryFn: () => api.indicatorsRange(interval, startMs, endMs),
        });
        if (requestEpoch !== dataEpochRef.current || interval !== activeIntervalRef.current) return;
        setIndicatorPoints((current) => mergeIndicators(current, olderIndicators as MarketIndicatorPoint[]));
      } finally {
        setIsLoadingMore(false);
      }
    },
    [interval, queryClient]
  );

  const handlePolymarketIntervalChange = useCallback((nextInterval: PolymarketInterval) => {
    setPolymarketInterval(nextInterval);
    setInterval(nextInterval);
  }, []);

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
          countdownTargetMs={selectedPolymarketWindow?.endMs ?? null}
        />
      </Card>
      {!isFullscreen && (
        <PolymarketBtcPanel
          interval={polymarketInterval}
          onIntervalChange={handlePolymarketIntervalChange}
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
        label: `${formatMarketTime(market)} · ${marketWindowLabel(market, markets)}`,
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
              {formatMarketTime(activeMarket)} · {marketWindowLabel(activeMarket, markets)} ·{" "}
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
  const displayPrice = formatCents(quote.buy_price ?? quote.best_ask ?? quote.price);
  return (
    <div className={`polymarket-outcome ${quote.name.toLowerCase() === "up" ? "up" : "down"}`}>
      <div className="polymarket-outcome-title">
        <span>{quote.name}</span>
        <strong className="polymarket-outcome-price">{displayPrice}</strong>
      </div>
      <div className="polymarket-quote-grid">
        <span className="polymarket-quote-item">
          <span>Sell</span>
          <strong>{formatCents(quote.sell_price ?? quote.best_bid)}</strong>
        </span>
        <span className="polymarket-quote-item">
          <span>Buy</span>
          <strong>{formatCents(quote.buy_price ?? quote.best_ask)}</strong>
        </span>
        <span className="polymarket-quote-item">
          <span>Last</span>
          <strong>{formatCents(quote.last_trade_price)}</strong>
        </span>
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

function nearestBaselineCandle(rows: MarketCandle[], startMs: number) {
  const candidates = rows
    .map((row) => ({ row, openMs: new Date(row.open_time).getTime() }))
    .filter(({ openMs }) => Number.isFinite(openMs) && openMs >= startMs)
    .sort((left, right) => Math.abs(left.openMs - startMs) - Math.abs(right.openMs - startMs));
  return candidates[0]?.row ?? null;
}

function marketComparisonLine({
  marketId,
  startMs,
  price,
  interval,
}: {
  marketId: string;
  startMs: number;
  price: number;
  interval: string;
}): ChartComparisonLine {
  return {
    id: `polymarket:${marketId}:${startMs}`,
    price,
    title: interval,
    color: "#f59e0b",
  };
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
  const window = polymarketDisplayWindow(market);
  if (!window) return market.window;
  const start = new Date(window.startMs);
  const end = new Date(window.endMs);
  const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${timeFormatter.format(start)}-${timeFormatter.format(end)}`;
}

function marketWindowLabel(market: PolymarketUpDownMarket, markets: PolymarketUpDownMarket[] = []) {
  const window = polymarketDisplayWindow(market);
  if (window) {
    const now = Date.now();
    if (window.startMs <= now && now < window.endMs) return "当前";
    if (now >= window.endMs) return "已结束";
    const nextMarket = markets
      .map((item) => ({ market: item, window: polymarketDisplayWindow(item) }))
      .filter((item): item is { market: PolymarketUpDownMarket; window: PolymarketDisplayWindow } =>
        Boolean(item.window && item.window.startMs > now)
      )
      .sort((left, right) => left.window.startMs - right.window.startMs)[0]?.market;
    return nextMarket?.id === market.id || market.window === "next" ? "下个" : "未来";
  }
  if (market.window === "current") return "当前";
  if (market.window === "next") return "下个";
  if (market.window === "upcoming") return "未来";
  if (market.window === "expired") return "已结束";
  return "未知";
}

type PolymarketDisplayWindow = {
  startMs: number;
  endMs: number;
};

function polymarketDisplayWindow(market: PolymarketUpDownMarket): PolymarketDisplayWindow | null {
  const titleWindow = polymarketTitleWindow(market);
  if (titleWindow) return titleWindow;

  const anchorMs = parseMarketTimeMs(market.start_time) ?? parseMarketTimeMs(market.end_time);
  const intervalMs = POLYMARKET_INTERVAL_MS[market.interval];
  if (anchorMs == null || !Number.isFinite(anchorMs) || !intervalMs) return null;
  const startMs = Math.floor(anchorMs / intervalMs) * intervalMs;
  return {
    startMs,
    endMs: startMs + intervalMs,
  };
}

function polymarketTitleWindow(market: PolymarketUpDownMarket): PolymarketDisplayWindow | null {
  const rangeMatch = market.title.match(
    /([A-Za-z]+)\s+(\d{1,2}),\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*ET/i
  );
  const singleMatch = market.title.match(/([A-Za-z]+)\s+(\d{1,2}),\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*ET/i);
  const match = rangeMatch ?? singleMatch;
  if (!match) return null;
  const [, monthName, dayText, startHourText, startMinuteText, startPeriod] = match;
  const month = MONTH_INDEX[monthName.toLowerCase()];
  const anchorMs = parseMarketTimeMs(market.start_time) ?? parseMarketTimeMs(market.end_time);
  if (month == null || anchorMs == null) return null;

  const year = new Date(anchorMs).getUTCFullYear();
  const day = Number(dayText);
  const startWallHour = toTwentyFourHour(Number(startHourText), startPeriod);
  const startWallMinute = Number(startMinuteText ?? "0");
  if ([day, startWallHour, startWallMinute].some((value) => !Number.isFinite(value))) {
    return null;
  }

  // Polymarket 标题里的 ET 窗口才是合约比较区间；API 时间字段可能是开盘/展示偏移。
  const startMs = zonedWallTimeToUtcMs(year, month, day, startWallHour, startWallMinute, ET_TIME_ZONE);
  if (!rangeMatch) {
    return { startMs, endMs: startMs + POLYMARKET_INTERVAL_MS[market.interval] };
  }

  const endWallHour = toTwentyFourHour(Number(rangeMatch[6]), rangeMatch[8]);
  const endWallMinute = Number(rangeMatch[7] ?? "0");
  if ([endWallHour, endWallMinute].some((value) => !Number.isFinite(value))) return null;
  let endMs = zonedWallTimeToUtcMs(year, month, day, endWallHour, endWallMinute, ET_TIME_ZONE);
  if (endMs <= startMs) endMs += 24 * 60 * ONE_MINUTE_MS;
  return { startMs, endMs };
}

function toTwentyFourHour(hour: number, period: string) {
  const normalized = hour % 12;
  return period.toUpperCase() === "PM" ? normalized + 12 : normalized;
}

function zonedWallTimeToUtcMs(year: number, month: number, day: number, hour: number, minute: number, timeZone: string) {
  const utcGuess = Date.UTC(year, month, day, hour, minute, 0, 0);
  return utcGuess - timeZoneOffsetMs(timeZone, utcGuess);
}

function timeZoneOffsetMs(timeZone: string, utcMs: number) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "shortOffset",
  });
  const offset = formatter.formatToParts(new Date(utcMs)).find((part) => part.type === "timeZoneName")?.value ?? "GMT";
  const match = offset.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
  if (!match) return 0;
  const [, sign, hourText, minuteText] = match;
  const minutes = Number(hourText) * 60 + Number(minuteText ?? "0");
  return (sign === "-" ? -1 : 1) * minutes * ONE_MINUTE_MS;
}

function parseMarketTimeMs(value: string | null): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric)) {
    return numeric > 1e12 ? numeric : numeric * 1000;
  }
  const parsed = new Date(trimmed).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}
