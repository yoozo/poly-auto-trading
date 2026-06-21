import { ClockCircleOutlined, DownOutlined, ExportOutlined, FullscreenExitOutlined, FullscreenOutlined } from "@ant-design/icons";
import { Button, Card, Dropdown, Empty, Modal, Segmented, Typography } from "antd";
import type { MenuProps } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CandleInterval,
  type PolymarketAccountOrder,
  type PolymarketAccountPosition,
  type PolymarketAccountState,
  type PolymarketAccountStateWsMessage,
  type PolymarketAccountTrade,
  type PolymarketInterval,
  type PolymarketOutcomeQuote,
  type PolymarketUpDownMarket,
  type PolymarketWsMessage,
} from "../api/client";
import BtcWatchChart from "../components/market-chart/BtcWatchChart";
import { buildCandlestickData } from "../components/market-chart/candlestickData";
import type {
  ChartComparisonLine,
  MarketCandle,
  MarketIndicatorPoint,
  StreamStatus,
} from "../components/market-chart/types";
import { INDICATOR_WARMUP_BARS, calculateIndicatorPoints } from "../components/market-chart/indicators";
import { intervalMs, mergeCandles } from "../components/market-chart/utils";
import {
  candleAtOpenTime,
  hasCandleAtTime,
  marketChartFocusKey,
  marketComparisonTarget,
  marketFocusAnchorMs,
  ONE_MINUTE_MS,
  POLYMARKET_INTERVAL_MS,
  polymarketDisplayWindow,
  selectedPolymarketMarket,
  type PolymarketDisplayWindow,
} from "./btcWatchMarketRules";

const intervals: CandleInterval[] = ["1m", "5m", "15m", "1h", "4h", "12h", "1d", "1w"];
const polymarketIntervals: PolymarketInterval[] = ["5m", "15m", "1h", "4h"];
const candleIntervalLabels: Record<CandleInterval, string> = {
  "1m": "1m",
  "5m": "5m",
  "15m": "15m",
  "30m": "30m",
  "1h": "1h",
  "4h": "4h",
  "12h": "12h",
  "1d": "1D",
  "1w": "1W",
};
const INTERVAL_KEY = "poly-auto.btcWatch.interval";
const BOLL_KEY = "poly-auto.btcWatch.boll";
const RSI_KEY = "poly-auto.btcWatch.rsi";
const POLY_INTERVAL_KEY = "poly-auto.btcWatch.polymarketInterval";
const MAX_VISIBLE_MARKET_PILLS = 5;
const COMPACT_VISIBLE_CANDLES = 50;
const WIDE_VISIBLE_CANDLES = 100;
const WIDE_LAYOUT_QUERY = "(min-width: 1361px)";
// React Query 加载期需要稳定空数组，避免 effect 依赖因内联 [] 新引用反复触发 setState。
const EMPTY_MARKET_CANDLES: MarketCandle[] = [];
const EMPTY_POLYMARKET_MARKETS: PolymarketUpDownMarket[] = [];
const EMPTY_ACCOUNT_STATE: PolymarketAccountState = {
  wallet: null,
  clob_address: null,
  balance: null,
  condition_id: null,
  positions: [],
  orders: [],
  recent_trades: [],
  ws_state: "idle",
  last_positions_refresh_at: null,
  last_orders_refresh_at: null,
  last_trade_at: null,
  error: null,
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
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSwitchingInterval, setIsSwitchingInterval] = useState(false);
  const [chartDataReady, setChartDataReady] = useState(false);
  const [chartEpoch, setChartEpoch] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [initialVisibleCandles, setInitialVisibleCandles] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(WIDE_LAYOUT_QUERY).matches
      ? WIDE_VISIBLE_CANDLES
      : COMPACT_VISIBLE_CANDLES
  );
  const [fitAnchorVersion, setFitAnchorVersion] = useState(0);
  const [polymarketInterval, setPolymarketInterval] = useState<PolymarketInterval>(() => {
    const saved = localStorage.getItem(POLY_INTERVAL_KEY);
    return polymarketIntervals.includes(saved as PolymarketInterval) ? (saved as PolymarketInterval) : "5m";
  });
  const [selectedPolymarketId, setSelectedPolymarketId] = useState<string | null>(null);
  const [autoSwitchPolymarket, setAutoSwitchPolymarket] = useState(true);
  const [polymarketFocusNonce, setPolymarketFocusNonce] = useState(0);
  const [polymarketFocusNowMs, setPolymarketFocusNowMs] = useState(() => Date.now());
  const [timeJumpInput, setTimeJumpInput] = useState(() => formatDateTimeLocalInput(new Date()));
  const [timeJumpFocus, setTimeJumpFocus] = useState<{ timeMs: number; nonce: number } | null>(null);
  const [timeJumpModalOpen, setTimeJumpModalOpen] = useState(false);
  const [isJumpingTime, setIsJumpingTime] = useState(false);
  const [timeJumpError, setTimeJumpError] = useState<string | null>(null);
  const [polymarketMarkets, setPolymarketMarkets] = useState<PolymarketUpDownMarket[]>([]);
  const [selectedPolymarketSnapshot, setSelectedPolymarketSnapshot] = useState<PolymarketUpDownMarket | null>(null);
  const [comparisonLine, setComparisonLine] = useState<ChartComparisonLine | null>(null);
  const [comparisonRetryNonce, setComparisonRetryNonce] = useState(0);
  const comparisonRequestKeyRef = useRef<string | null>(null);
  const activeComparisonKeyRef = useRef<string | null>(null);
  const comparisonLineCacheRef = useRef<Map<string, ChartComparisonLine>>(new Map());
  const comparisonRetryTimerRef = useRef<number | null>(null);
  const marketFocusDataRequestKeyRef = useRef<string | null>(null);
  const dataEpochRef = useRef(0);
  const activeIntervalRef = useRef<CandleInterval>(interval);
  const intervalActivatedAtRef = useRef(Date.now());
  const candleSnapshotReadyRef = useRef(false);
  const historicalJumpViewRef = useRef(false);
  const pendingLiveCandlesRef = useRef<MarketCandle[]>([]);

  useEffect(() => {
    const mediaQuery = window.matchMedia(WIDE_LAYOUT_QUERY);
    const syncVisibleCandles = () => {
      const nextVisibleCandles = mediaQuery.matches ? WIDE_VISIBLE_CANDLES : COMPACT_VISIBLE_CANDLES;
      setInitialVisibleCandles((current) => {
        if (current === nextVisibleCandles) return current;
        setFitAnchorVersion((version) => version + 1);
        return nextVisibleCandles;
      });
    };
    syncVisibleCandles();
    mediaQuery.addEventListener("change", syncVisibleCandles);
    return () => mediaQuery.removeEventListener("change", syncVisibleCandles);
  }, []);

  const {
    data: polymarketSnapshot = EMPTY_POLYMARKET_MARKETS,
    error: polymarketError,
    isFetched: polymarketSnapshotFetched,
  } = useQuery({
    queryKey: ["polymarket-btc-up-down", polymarketInterval],
    queryFn: () => api.polymarketBtcUpDown(polymarketInterval, 12),
    // REST 只负责初始快照和切换 interval；后续盘口/窗口变化由 Polymarket WS 快照推送。
    staleTime: 5 * ONE_MINUTE_MS,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const activeCandles = useMemo(() => candles.filter((candle) => candle.interval === interval), [candles, interval]);
  const activeIndicators = useMemo(
    () => calculateIndicatorPoints(activeCandles, interval),
    [activeCandles, interval]
  );
  const latest = activeCandles.at(-1);
  const selectedPolymarket = selectedPolymarketMarket({
    markets: polymarketMarkets,
    selectedMarketId: selectedPolymarketId,
    selectedMarketSnapshot: selectedPolymarketSnapshot,
  });
  const selectedPolymarketWindow = selectedPolymarket ? polymarketDisplayWindow(selectedPolymarket) : null;
  const comparisonTarget = selectedPolymarket ? marketComparisonTarget(selectedPolymarket) : null;
  const selectedPolymarketConditionId = selectedPolymarket?.condition_id ?? null;
  const {
    data: accountStateSnapshot = EMPTY_ACCOUNT_STATE,
    error: accountStateError,
  } = useQuery({
    queryKey: ["polymarket-account-state", selectedPolymarketConditionId],
    queryFn: () => api.polymarketAccountState(selectedPolymarketConditionId),
    enabled: Boolean(selectedPolymarketConditionId),
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
  });
  const [polymarketAccountState, setPolymarketAccountState] = useState<PolymarketAccountState>(EMPTY_ACCOUNT_STATE);
  const polymarketChartFocusAnchorMs = selectedPolymarketWindow
    ? marketFocusAnchorMs(selectedPolymarketWindow, interval, polymarketFocusNowMs)
    : null;
  const chartFocusTimeMs = timeJumpFocus?.timeMs ?? polymarketChartFocusAnchorMs;
  // focusKey 表示“用户要求图表重新定位”的版本；market 定位用触发时的当前时间，target 线仍用窗口起点。
  const chartFocusKey = timeJumpFocus
    ? `time-jump:${timeJumpFocus.nonce}`
    : polymarketChartFocusAnchorMs !== null && selectedPolymarket
      ? marketChartFocusKey({
          nonce: polymarketFocusNonce,
          marketId: selectedPolymarket.id,
          focusAnchorMs: polymarketChartFocusAnchorMs,
          candleInterval: interval,
        })
      : null;
  const candleSnapshotEnabled = Boolean(
    timeJumpFocus ||
      selectedPolymarket ||
      polymarketError ||
      (polymarketSnapshotFetched && polymarketSnapshot.length === 0 && polymarketMarkets.length === 0)
  );
  const candleSnapshotQuery = useMemo(() => {
    if (timeJumpFocus || polymarketChartFocusAnchorMs === null || !Number.isFinite(polymarketChartFocusAnchorMs)) {
      return {
        mode: "latest" as const,
        queryKey: ["candles", interval, "latest"] as const,
        queryFn: (signal: AbortSignal) => api.candles(interval, 300 + INDICATOR_WARMUP_BARS, { signal }),
      };
    }

    const barMs = intervalMs(interval);
    const visibleBars = Math.max(initialVisibleCandles, 80);
    const startMs = Math.max(0, polymarketChartFocusAnchorMs - Math.round(visibleBars * 0.45) * barMs);
    const endMs = polymarketChartFocusAnchorMs + Math.round(visibleBars * 0.65) * barMs;
    const limit = Math.min(Math.max(visibleBars + 40, 140), 1000);
    const warmupStartMs = withIndicatorWarmupStart(startMs, interval);

    return {
      mode: "focus" as const,
      queryKey: ["candles", interval, "focus", warmupStartMs, endMs, limit + INDICATOR_WARMUP_BARS] as const,
      queryFn: (signal: AbortSignal) =>
        api.candlesRange(interval, warmupStartMs, endMs, limit + INDICATOR_WARMUP_BARS, { signal }),
    };
  }, [initialVisibleCandles, interval, polymarketChartFocusAnchorMs, timeJumpFocus]);
  const { data: latestCandles = EMPTY_MARKET_CANDLES, dataUpdatedAt: latestCandlesUpdatedAt, error } = useQuery({
    queryKey: candleSnapshotQuery.queryKey,
    queryFn: ({ signal }) => candleSnapshotQuery.queryFn(signal),
    enabled: candleSnapshotEnabled,
    // K 线实时增量由 WS 维护；REST 只负责初始窗口/切换周期，避免浏览器聚焦时补打重复快照。
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const marketPriceDiff = latest && comparisonLine ? latest.close - comparisonLine.price : null;
  const marketDiffTone =
    marketPriceDiff !== null && marketPriceDiff > 0 ? "up" : marketPriceDiff !== null && marketPriceDiff < 0 ? "down" : "flat";
  const marketDiffInterval = selectedPolymarket?.interval ?? polymarketInterval;

  useEffect(() => {
    localStorage.setItem(INTERVAL_KEY, interval);
  }, [interval]);

  useEffect(() => {
    if (error) setIsSwitchingInterval(false);
  }, [error]);

  useEffect(() => {
    if (chartDataReady) return;
    if (buildCandlestickData(activeCandles, interval).data.length > 0) {
      // 图表只在当前周期至少有一批合法 K 线后挂载，避免旧 series 接收空帧。
      setChartDataReady(true);
    }
  }, [activeCandles, chartDataReady, interval]);

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
    setSelectedPolymarketSnapshot(null);
    setComparisonLine(null);
    comparisonRequestKeyRef.current = null;
    activeComparisonKeyRef.current = null;
    comparisonLineCacheRef.current.clear();
  }, [polymarketInterval]);

  useEffect(() => {
    setPolymarketMarkets(polymarketSnapshot);
  }, [polymarketSnapshot]);

  useEffect(() => {
    if (!selectedPolymarketConditionId) {
      setPolymarketAccountState(EMPTY_ACCOUNT_STATE);
      return;
    }
    setPolymarketAccountState(accountStateSnapshot);
  }, [accountStateSnapshot, selectedPolymarketConditionId]);

  useEffect(() => {
    if (!selectedPolymarketId) {
      setSelectedPolymarketSnapshot(null);
      return;
    }
    const freshMarket = polymarketMarkets.find((market) => market.id === selectedPolymarketId);
    if (freshMarket) setSelectedPolymarketSnapshot(freshMarket);
  }, [polymarketMarkets, selectedPolymarketId]);

  useEffect(() => {
    if (!selectedPolymarketConditionId) return;
    let socket: WebSocket | null = null;
    let connectTimer = 0;
    let reconnectTimer = 0;
    let closedByEffect = false;
    const conditionId = selectedPolymarketConditionId;

    const connect = () => {
      if (closedByEffect) return;
      socket = new WebSocket(api.polymarketAccountStateWsUrl(conditionId));
      socket.onmessage = (event) => {
        const message = parsePolymarketAccountMessage(event.data);
        if (!message || message.condition_id !== conditionId) return;
        setPolymarketAccountState(message.state);
      };
      socket.onclose = () => {
        if (closedByEffect) return;
        reconnectTimer = window.setTimeout(connect, 1000);
      };
    };

    connectTimer = window.setTimeout(connect, 0);
    return () => {
      closedByEffect = true;
      if (connectTimer) window.clearTimeout(connectTimer);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [selectedPolymarketConditionId]);

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
    if (selectedPolymarketId && !selected && !autoSwitchPolymarket) return;
    const selectedWindowLabel = selected ? marketWindowLabel(selected, polymarketMarkets) : null;
    if (selectedWindowLabel === "当前" && !autoSwitchPolymarket) {
      setAutoSwitchPolymarket(true);
      return;
    }
    if (selected && !autoSwitchPolymarket) return;
    const nextMarket =
      polymarketMarkets.find((market) => marketWindowLabel(market, polymarketMarkets) === "当前") ??
      polymarketMarkets.find((market) => marketWindowLabel(market, polymarketMarkets) === "下个") ??
      polymarketMarkets.find((market) => marketWindowLabel(market, polymarketMarkets) === "未来") ??
      polymarketMarkets[0];
    if (nextMarket && nextMarket.id !== selectedPolymarketId) {
      setPolymarketFocusNowMs(Date.now());
      setPolymarketFocusNonce((value) => value + 1);
      setSelectedPolymarketId(nextMarket.id);
    }
  }, [autoSwitchPolymarket, polymarketMarkets, selectedPolymarketId]);

  useEffect(() => {
    let cancelled = false;
    const comparisonKey = comparisonTarget?.key ?? null;
    activeComparisonKeyRef.current = comparisonKey;
    clearComparisonRetryTimer();

    if (!comparisonTarget || !comparisonKey) {
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
    const { baselineStartMs, marketId, marketInterval } = comparisonTarget;
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
          Math.max(0, baselineStartMs),
          baselineStartMs + 5 * ONE_MINUTE_MS,
          6
        );
        if (cancelled || activeComparisonKeyRef.current !== comparisonKey) return;
        const targetCandle = candleAtOpenTime(rows, baselineStartMs);
        if (!targetCandle || !Number.isFinite(targetCandle.open)) {
          if (comparisonRequestKeyRef.current === comparisonKey) {
            comparisonRequestKeyRef.current = null;
          }
          setComparisonLine(null);
          scheduleComparisonRetry(comparisonKey);
          return;
        }
        const nextLine = marketComparisonLine({
          marketId,
          startMs: baselineStartMs,
          price: targetCandle.open,
          interval: marketInterval,
        });
        comparisonLineCacheRef.current.set(comparisonKey, nextLine);
        setComparisonLine(nextLine);
      } catch {
        if (!cancelled && activeComparisonKeyRef.current === comparisonKey) {
          if (comparisonRequestKeyRef.current === comparisonKey) {
            comparisonRequestKeyRef.current = null;
          }
          setComparisonLine(null);
          scheduleComparisonRetry(comparisonKey);
        }
      }
    })();

    return () => {
      cancelled = true;
      clearComparisonRetryTimer();
      if (comparisonRequestKeyRef.current === comparisonKey && !comparisonLineCacheRef.current.has(comparisonKey)) {
        comparisonRequestKeyRef.current = null;
      }
    };
  }, [
    comparisonTarget?.key,
    comparisonTarget?.baselineStartMs,
    comparisonTarget?.marketId,
    comparisonTarget?.marketInterval,
    comparisonRetryNonce,
  ]);

  function clearComparisonRetryTimer() {
    if (comparisonRetryTimerRef.current === null) return;
    window.clearTimeout(comparisonRetryTimerRef.current);
    comparisonRetryTimerRef.current = null;
  }

  function scheduleComparisonRetry(comparisonKey: string) {
    clearComparisonRetryTimer();
    // 1m 起点 candle 可能刚生成或接口短暂失败；只重试请求，不放宽精确 open_time 规则。
    comparisonRetryTimerRef.current = window.setTimeout(() => {
      comparisonRetryTimerRef.current = null;
      if (activeComparisonKeyRef.current === comparisonKey && !comparisonLineCacheRef.current.has(comparisonKey)) {
        setComparisonRetryNonce((value) => value + 1);
      }
    }, 3000);
  }

  useEffect(() => {
    if (timeJumpFocus || !chartFocusKey || polymarketChartFocusAnchorMs === null || !Number.isFinite(polymarketChartFocusAnchorMs)) {
      return;
    }
    if (!candleSnapshotReadyRef.current) {
      return;
    }
    const focusAnchorMs = polymarketChartFocusAnchorMs;
    if (hasCandleAtTime(activeCandles, focusAnchorMs)) {
      marketFocusDataRequestKeyRef.current = null;
      return;
    }
    if (candleSnapshotQuery.mode === "focus") {
      marketFocusDataRequestKeyRef.current = null;
      return;
    }

    const requestKey = `${chartFocusKey}:${interval}`;
    if (marketFocusDataRequestKeyRef.current === requestKey) return;
    marketFocusDataRequestKeyRef.current = requestKey;

    const requestEpoch = dataEpochRef.current;
    const requestInterval = activeIntervalRef.current;
    const barMs = intervalMs(requestInterval);
    const visibleBars = Math.max(initialVisibleCandles, 80);
    const startMs = Math.max(0, focusAnchorMs - Math.round(visibleBars * 0.45) * barMs);
    const endMs = focusAnchorMs + Math.round(visibleBars * 0.65) * barMs;
    const limit = Math.min(Math.max(visibleBars + 40, 140), 1000);

    // 首屏 REST 快照先定基准；快照内仍缺目标 open_time 时，再按当前 K 线周期补齐锚点附近数据。
    void (async () => {
      try {
        const focusCandlesResult = await Promise.resolve(
          api.candlesRange(requestInterval, withIndicatorWarmupStart(startMs, requestInterval), endMs, limit + INDICATOR_WARMUP_BARS)
        )
          .then((value) => ({ status: "fulfilled" as const, value }))
          .catch((reason) => ({ status: "rejected" as const, reason }));
        if (requestEpoch !== dataEpochRef.current || requestInterval !== activeIntervalRef.current) return;
        if (focusCandlesResult.status === "rejected") {
          if (marketFocusDataRequestKeyRef.current === requestKey) {
            marketFocusDataRequestKeyRef.current = null;
          }
          return;
        }
        const focusCandles = focusCandlesResult.value;
        if (focusCandles.length <= 0) {
          if (marketFocusDataRequestKeyRef.current === requestKey) {
            marketFocusDataRequestKeyRef.current = null;
          }
          return;
        }
        setCandles((current) => mergeCandles(current, focusCandles));
        setChartDataReady(true);
      } catch {
        if (marketFocusDataRequestKeyRef.current === requestKey) {
          marketFocusDataRequestKeyRef.current = null;
        }
      }
    })();
  }, [activeCandles, candleSnapshotQuery.mode, chartFocusKey, initialVisibleCandles, interval, polymarketChartFocusAnchorMs, timeJumpFocus]);

  useEffect(() => {
    const requestEpoch = dataEpochRef.current;
    if (historicalJumpViewRef.current) return;
    // 切回曾经看过的周期时 React Query 会先吐旧缓存；定位只允许使用本次切换后完成的快照。
    if (latestCandlesUpdatedAt < intervalActivatedAtRef.current) {
      return;
    }
    candleSnapshotReadyRef.current = true;
    const pendingLiveCandles = pendingLiveCandlesRef.current;
    pendingLiveCandlesRef.current = [];
    setCandles((current) => {
      if (requestEpoch !== dataEpochRef.current) return current;
      return mergeCandles(current, [...latestCandles, ...pendingLiveCandles]);
    });
    setIsSwitchingInterval(false);
  }, [latestCandles, latestCandlesUpdatedAt]);

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
        if (historicalJumpViewRef.current) return;
        const candle = message.candle;
        if (!candleSnapshotReadyRef.current) {
          // 切换周期后的第一帧必须由 REST 快照决定窗口宽度；WS 单根 K 线先缓冲，避免先锚到错误位置再跳回。
          if (candle) pendingLiveCandlesRef.current = mergeCandles(pendingLiveCandlesRef.current, [candle]);
          return;
        }
        if (candle) {
          setCandles((current) => mergeCandles(current, [candle]));
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
        // 指标由前端基于 candle 计算，历史翻页只需要多取 warmup K 线。
        const requestEpoch = dataEpochRef.current;
        const older = await api.candlesRange(interval, withIndicatorWarmupStart(startMs, interval), endMs);
        if (requestEpoch !== dataEpochRef.current || interval !== activeIntervalRef.current) {
          return;
        }
        setCandles((current) => mergeCandles(current, older));
      } finally {
        setIsLoadingMore(false);
      }
    },
    [interval]
  );

  const switchCandleInterval = useCallback(
    (nextInterval: CandleInterval) => {
      if (nextInterval === activeIntervalRef.current) return;
      // 必须在 setState 前同步推进 epoch；否则切回旧周期时，React Query 的旧缓存可能先参与图表重锚。
      activeIntervalRef.current = nextInterval;
      intervalActivatedAtRef.current = Date.now();
      dataEpochRef.current += 1;
      historicalJumpViewRef.current = false;
      candleSnapshotReadyRef.current = false;
      pendingLiveCandlesRef.current = [];
      marketFocusDataRequestKeyRef.current = null;
      setPolymarketFocusNowMs(Date.now());
      // 切换目标周期时丢弃旧快照，强制首屏定位只基于本次切换后的 REST/WS 数据。
      queryClient.removeQueries({ queryKey: ["candles", nextInterval], exact: true });
      setCandles([]);
      setChartDataReady(false);
      setChartEpoch((epoch) => epoch + 1);
      setIsLoadingMore(false);
      setIsSwitchingInterval(true);
      setInterval(nextInterval);
    },
    [queryClient]
  );

  const handlePolymarketIntervalChange = useCallback((nextInterval: PolymarketInterval) => {
    // 用户切换 Polymarket 粒度时，以当前时间作为本次图表定位锚点。
    historicalJumpViewRef.current = false;
    setTimeJumpFocus(null);
    setPolymarketFocusNowMs(Date.now());
    setPolymarketFocusNonce((value) => value + 1);
    setPolymarketInterval(nextInterval);
    switchCandleInterval(nextInterval);
  }, [switchCandleInterval]);

  const handlePolymarketMarketSelect = useCallback(
    (marketId: string, followCurrent = false) => {
      setTimeJumpFocus(null);
      historicalJumpViewRef.current = false;
      setPolymarketFocusNowMs(Date.now());
      setPolymarketFocusNonce((value) => value + 1);
      setSelectedPolymarketSnapshot(polymarketMarkets.find((market) => market.id === marketId) ?? null);
      setSelectedPolymarketId(marketId);
      // 选中当前窗口代表回到实时跟随；选中历史或未来窗口则固定查看该窗口。
      setAutoSwitchPolymarket(followCurrent);
    },
    [polymarketMarkets]
  );

  const candleIntervalOptions = useMemo(
    () =>
      intervals.map((item) => {
        const canSwitchMarket = polymarketIntervals.includes(item as PolymarketInterval);
        const isMarketInterval = canSwitchMarket && item === polymarketInterval;
        return {
          value: item,
          label: (
            <span
              className={isMarketInterval ? "watch-interval-option market-selected" : "watch-interval-option"}
              title={canSwitchMarket ? "点不同周期切 K 线；点当前周期选择 market" : "单击切换 K 线"}
              onClick={() => {
                if (canSwitchMarket && item === interval) handlePolymarketIntervalChange(item as PolymarketInterval);
              }}
            >
              {candleIntervalLabels[item]}
            </span>
          ),
        };
      }),
    [handlePolymarketIntervalChange, interval, polymarketInterval]
  );

  const handleTimeJump = useCallback(async () => {
    const targetMs = parseDateTimeLocalInput(timeJumpInput);
    if (targetMs === null) {
      setTimeJumpError("请选择有效时间");
      return;
    }
    const requestEpoch = dataEpochRef.current;
    const jumpInterval = activeIntervalRef.current;
    const barMs = intervalMs(jumpInterval);
    const visibleBars = Math.max(initialVisibleCandles, 80);
    const startMs = Math.max(0, targetMs - Math.round(visibleBars * 0.45) * barMs);
    const endMs = targetMs + Math.round(visibleBars * 0.65) * barMs;
    const limit = Math.min(Math.max(visibleBars + 40, 140), 1000);

    setIsJumpingTime(true);
    setTimeJumpError(null);
    try {
      // 时间跳转可能落在当前缓存窗口之外，先补齐目标附近和指标 warmup 所需的 candle。
      const jumpCandles = await api.candlesRange(
        jumpInterval,
        withIndicatorWarmupStart(startMs, jumpInterval),
        endMs,
        limit + INDICATOR_WARMUP_BARS
      );
      if (requestEpoch !== dataEpochRef.current || jumpInterval !== activeIntervalRef.current) return;
      if (jumpCandles.length <= 0) {
        setTimeJumpError("该时间附近暂无 K 线");
        return;
      }
      // 跳转到历史时间时只展示目标附近的连续窗口，避免和实时窗口跨天拼接成断裂曲线。
      historicalJumpViewRef.current = true;
      setCandles((current) => mergeCandles(current.filter((candle) => candle.interval !== jumpInterval), jumpCandles));
      setChartDataReady(true);
      setTimeJumpFocus({ timeMs: targetMs, nonce: Date.now() });
      setTimeJumpModalOpen(false);
    } catch (error) {
      setTimeJumpError(error instanceof Error ? error.message : "跳转失败");
    } finally {
      setIsJumpingTime(false);
    }
  }, [initialVisibleCandles, timeJumpInput]);

  const toggleFullscreen = useCallback(() => {
    setIsFullscreen((value) => {
      const nextValue = !value;
      setFitAnchorVersion((version) => version + 1);
      return nextValue;
    });
  }, []);

  const chartToolbar = (
    <div className="watch-toolbar-inner">
      <div className="watch-toolbar-controls">
        <Typography.Text strong>BTCUSDT</Typography.Text>
        <Segmented
          size="small"
          options={candleIntervalOptions}
          value={interval}
          onChange={(value) => switchCandleInterval(value as CandleInterval)}
        />
        <Button
          className={showBollinger ? "watch-indicator-button active" : "watch-indicator-button"}
          size="small"
          aria-pressed={showBollinger}
          onClick={() => setShowBollinger((value) => !value)}
        >
          BOLL
        </Button>
        <Button
          className={showRsi ? "watch-indicator-button active" : "watch-indicator-button"}
          size="small"
          aria-pressed={showRsi}
          onClick={() => setShowRsi((value) => !value)}
        >
          RSI
        </Button>
      </div>
      <div className="watch-toolbar-status">
        {latest && (
          <Typography.Text className={`watch-market-diff watch-market-diff-${marketDiffTone}`}>
            <span className="watch-market-diff-interval">{marketDiffInterval}</span>
            <span className="watch-market-diff-value">
              {marketPriceDiff === null ? (
                "--"
              ) : (
                <>
                  <span className="watch-market-diff-arrow">{marketPriceDiff >= 0 ? "▲" : "▼"}</span>
                  <span>${formatMarketPriceDiff(marketPriceDiff)}</span>
                </>
              )}
            </span>
          </Typography.Text>
        )}
        {isLoadingMore && <Typography.Text type="secondary">加载历史中...</Typography.Text>}
        {error instanceof Error && <Typography.Text type="danger">{error.message}</Typography.Text>}
      </div>
      <Button
        className="watch-fullscreen-button"
        size="small"
        icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
        onClick={toggleFullscreen}
        aria-label={isFullscreen ? "退出全屏" : "全屏"}
        title={isFullscreen ? "退出全屏" : "全屏"}
      />
    </div>
  );

  const timeJumpControl = (
    <Button
      className="chart-time-jump-trigger"
      size="small"
      icon={<ClockCircleOutlined />}
      onClick={() => setTimeJumpModalOpen(true)}
      aria-label="跳转到指定时间"
      title="跳转到指定时间"
    />
  );

  return (
    <div className={isFullscreen ? "watch-page watch-page-fullscreen" : "watch-page"}>
      <Card className="watch-chart-card btc-watch-card" styles={{ body: { padding: 0 } }}>
        {chartDataReady ? (
          <BtcWatchChart
            key={`BTCUSDT-${interval}-${chartEpoch}`}
            symbol="BTCUSDT"
            interval={interval}
            candles={activeCandles}
            indicators={activeIndicators}
            showBollinger={showBollinger}
            showRsi={showRsi}
            onLoadMore={loadMore}
            isLoadingMore={isLoadingMore}
            isInitializing={isSwitchingInterval}
            latestStreamStatus={streamStatus}
            fitAnchorVersion={fitAnchorVersion}
            initialVisibleCandles={initialVisibleCandles}
            comparisonLine={comparisonLine}
            focusTimeMs={chartFocusTimeMs}
            focusKey={chartFocusKey}
            focusPlacement={timeJumpFocus ? "center" : "anchor"}
            countdownTargetMs={selectedPolymarketWindow?.endMs ?? null}
            toolbar={chartToolbar}
            timeAxisLeftControl={timeJumpControl}
          />
        ) : (
          <div className={["btc-watch-chart", showRsi ? "" : "btc-watch-chart-single", "btc-watch-chart-initializing"].filter(Boolean).join(" ")}>
            {chartToolbar && <div className="btc-chart-toolbar">{chartToolbar}</div>}
            <section className="btc-chart-panel btc-main-panel">
              <div className="btc-chart-canvas btc-main-chart">
                <div className="chart-loading-overlay">加载 K 线...</div>
              </div>
            </section>
            <section className="btc-chart-panel btc-rsi-panel" hidden={!showRsi}>
              <div className="btc-chart-canvas btc-rsi-chart" />
            </section>
            <section className="btc-chart-panel btc-diff-panel" hidden={!showRsi}>
              <div className="btc-chart-canvas btc-diff-chart" />
            </section>
          </div>
        )}
      </Card>
      {!isFullscreen && (
        <PolymarketBtcPanel
          interval={polymarketInterval}
          markets={polymarketMarkets}
          selectedMarket={selectedPolymarket}
          selectedMarketId={selectedPolymarketId}
          onSelectedMarketId={handlePolymarketMarketSelect}
          error={polymarketError instanceof Error ? polymarketError.message : null}
          accountState={polymarketAccountState}
          accountStateError={accountStateError instanceof Error ? accountStateError.message : null}
        />
      )}
      <Modal
        title="跳转到时间"
        open={timeJumpModalOpen}
        okText="确定"
        cancelText="取消"
        confirmLoading={isJumpingTime}
        onOk={() => void handleTimeJump()}
        onCancel={() => {
          if (!isJumpingTime) setTimeJumpModalOpen(false);
        }}
      >
        <form
          className="chart-time-jump-modal"
          onSubmit={(event) => {
            event.preventDefault();
            void handleTimeJump();
          }}
        >
          <input
            className="chart-time-jump-input"
            type="datetime-local"
            value={timeJumpInput}
            onChange={(event) => {
              setTimeJumpInput(event.target.value);
              setTimeJumpError(null);
            }}
            aria-label="跳转时间"
            autoFocus
          />
          {timeJumpError && <Typography.Text type="danger">{timeJumpError}</Typography.Text>}
        </form>
      </Modal>
    </div>
  );
}

function PolymarketBtcPanel({
  interval,
  markets,
  selectedMarket,
  selectedMarketId,
  onSelectedMarketId,
  error,
  accountState,
  accountStateError,
}: {
  interval: PolymarketInterval;
  markets: PolymarketUpDownMarket[];
  selectedMarket: PolymarketUpDownMarket | undefined;
  selectedMarketId: string | null;
  onSelectedMarketId: (marketId: string, followCurrent?: boolean) => void;
  error: string | null;
  accountState: PolymarketAccountState;
  accountStateError: string | null;
}) {
  const activeMarket =
    markets.find((market) => market.id === selectedMarketId) ??
    selectedMarket ??
    markets.find((market) => market.window === "current") ??
    markets.find((market) => market.window === "next") ??
    markets[0];
  const railModel = useMemo(
    () => buildMarketRailModel(markets, activeMarket?.id, selectedMarketId),
    [activeMarket?.id, markets, selectedMarketId]
  );
  const moreMenuItems = useMemo<MenuProps["items"]>(
    () =>
      railModel.moreMarkets.map((market) => ({
        key: market.id,
        label: formatMarketEndTime(market),
      })),
    [railModel.moreMarkets]
  );

  return (
    <Card className="polymarket-panel" styles={{ body: { padding: 12 } }}>
      <div className="polymarket-panel-head">
        <div className="polymarket-panel-title">
          <div className="polymarket-title-row">
            {activeMarket?.slug ? (
              <a
                className="polymarket-panel-title-link"
                href={`https://polymarket.com/event/${activeMarket.slug}`}
                target="_blank"
                rel="noreferrer"
              >
                <span>Polymarket BTC Up/Down</span>
                <ExportOutlined />
              </a>
            ) : (
              <Typography.Text strong>Polymarket BTC Up/Down</Typography.Text>
            )}
          </div>
          {activeMarket && (
            <Typography.Text type="secondary" className="polymarket-panel-subtitle">
              {formatMarketTime(activeMarket)} · {marketWindowLabel(activeMarket, markets)} ·{" "}
              {activeMarket.accepting_orders ? "可交易" : "暂停接单"} · 流动性{" "}
              {formatCompact(activeMarket.liquidity)}
            </Typography.Text>
          )}
        </div>
      </div>
      <div className="polymarket-market-selector">
        <div className="polymarket-market-rail" role="tablist" aria-label="Polymarket 市场窗口">
          {railModel.latestPastMarket && (
            <button
              type="button"
              className={railModel.latestPastMarket.id === activeMarket?.id ? "polymarket-market-pill active" : "polymarket-market-pill"}
              onClick={() => onSelectedMarketId(railModel.latestPastMarket!.id, false)}
              aria-pressed={railModel.latestPastMarket.id === activeMarket?.id}
            >
              <span className="polymarket-market-pill-time">{formatMarketEndTime(railModel.latestPastMarket)}</span>
            </button>
          )}
          {railModel.visibleMarkets.map((market) => {
            const isActive = market.id === activeMarket?.id;
            const isLive = marketWindowLabel(market, markets) === "当前";
            return (
              <button
                key={market.id}
                type="button"
                className={isActive ? "polymarket-market-pill active" : "polymarket-market-pill"}
                onClick={() => onSelectedMarketId(market.id, isLive)}
                aria-pressed={isActive}
              >
                {isLive && <span className="polymarket-market-pill-live-dot" aria-hidden="true" />}
                <span className="polymarket-market-pill-time">{formatMarketEndTime(market)}</span>
              </button>
            );
          })}
          {moreMenuItems && moreMenuItems.length > 0 && (
            <Dropdown
              menu={{
                items: moreMenuItems,
                selectable: false,
                onClick: ({ key }) => {
                  const market = railModel.moreMarkets.find((item) => item.id === String(key));
                  onSelectedMarketId(String(key), market ? marketWindowLabel(market, markets) === "当前" : false);
                },
              }}
              trigger={["click"]}
            >
              <Button className="polymarket-market-pill polymarket-market-pill-more" size="small">
                More <DownOutlined />
              </Button>
            </Dropdown>
          )}
        </div>
      </div>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
      {!activeMarket && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`暂无 ${interval} 市场`} />}
      {activeMarket && (
        <div className="polymarket-market">
          <div className="polymarket-outcomes">
            {activeMarket.outcome_quotes.map((quote) => (
              <OutcomeQuoteCard key={`${activeMarket.id}:${quote.name}`} quote={quote} />
            ))}
          </div>
          <AccountStatePanel
            market={activeMarket}
            accountState={accountState}
            error={accountStateError}
          />
        </div>
      )}
    </Card>
  );
}

function AccountStatePanel({
  market,
  accountState,
  error,
}: {
  market: PolymarketUpDownMarket;
  accountState: PolymarketAccountState;
  error: string | null;
}) {
  const positions = accountState.positions.filter((position) => positionMatchesMarket(position, market));
  const orders = accountState.orders.filter((order) => orderMatchesMarket(order, market));
  const trades = accountState.recent_trades.filter((trade) => tradeMatchesMarket(trade, market));
  const wsLabel = accountStateStatusLabel(accountState.ws_state);
  const queryClient = useQueryClient();
  const [cancelingOrderId, setCancelingOrderId] = useState<string | null>(null);
  const [orderNotice, setOrderNotice] = useState<string | null>(null);
  const accountStateQueryKey = ["polymarket-account-state", market.condition_id ?? null];
  const cancelOrderMutation = useMutation({
    mutationFn: api.cancelPolymarketOrder,
    onMutate: (orderId: string) => {
      setCancelingOrderId(orderId);
      setOrderNotice(null);
    },
    onSuccess: () => {
      setOrderNotice("撤单已提交");
      queryClient.invalidateQueries({ queryKey: accountStateQueryKey });
    },
    onError: (mutationError) => {
      setOrderNotice(errorMessage(mutationError));
    },
    onSettled: () => {
      setCancelingOrderId(null);
    },
  });
  return (
    <div className="polymarket-account-panel">
      <div className="polymarket-account-head">
        <Typography.Text strong>我的账户</Typography.Text>
        <Typography.Text type="secondary">{wsLabel}</Typography.Text>
      </div>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
      {accountState.error && <Typography.Text type="secondary">{accountState.error}</Typography.Text>}
      <AccountPositionSection positions={positions} />
      <AccountTradeSection trades={trades} />
      {orderNotice && (
        <Typography.Text className="polymarket-order-notice" type={orderNotice.includes("失败") ? "danger" : "secondary"}>
          {orderNotice}
        </Typography.Text>
      )}
      <AccountOrderSection
        orders={orders}
        cancelingOrderId={cancelingOrderId}
        onCancelOrder={(orderId) => cancelOrderMutation.mutate(orderId)}
      />
    </div>
  );
}

function AccountTradeSection({ trades }: { trades: PolymarketAccountTrade[] }) {
  return (
    <div className="polymarket-account-section">
      <div className="polymarket-account-section-title">最近成交 / 待确认</div>
      {trades.length === 0 ? (
        <Typography.Text type="secondary">当前 market 暂无成交事件</Typography.Text>
      ) : (
        <div className="polymarket-account-table polymarket-trade-table">
          <div className="polymarket-account-table-head">
            <span>Side</span>
            <span>Outcome</span>
            <span>Price</span>
            <span>Size</span>
            <span>Time</span>
            <span>Status</span>
          </div>
          {trades.map((trade) => (
            <div className="polymarket-account-row" key={trade.id}>
              <span className="polymarket-account-side">{formatOrderSide(trade.side)}</span>
              <span className="polymarket-account-outcome-cell">
                <span className={outcomePillClassName(trade.outcome)}>{trade.outcome ?? trade.asset_id ?? "-"}</span>
              </span>
              <span>{formatCents(trade.price)}</span>
              <span>{formatSize(trade.size)}</span>
              <span>{formatTradeTime(trade.timestamp ?? trade.received_at)}</span>
              <span className={tradeStatusClassName(trade.confirmation_status)}>
                {tradeStatusLabel(trade.confirmation_status)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AccountPositionSection({ positions }: { positions: PolymarketAccountPosition[] }) {
  return (
    <div className="polymarket-account-section">
      <div className="polymarket-account-section-title">我的仓位</div>
      {positions.length === 0 ? (
        <Typography.Text type="secondary">当前 market 暂无仓位</Typography.Text>
      ) : (
        <div className="polymarket-account-table polymarket-position-table">
          <div className="polymarket-account-table-head">
            <span>Outcome</span>
            <span>Qty</span>
            <span>Avg</span>
            <span>Value</span>
          </div>
          {positions.map((position) => (
            <div className="polymarket-account-row" key={`${position.condition_id}:${position.asset}:${position.outcome}`}>
              <span className="polymarket-account-outcome-cell">
                <span className={outcomePillClassName(position.outcome)}>{position.outcome ?? position.asset ?? "-"}</span>
              </span>
              <span>{formatSize(position.size)}</span>
              <span>{formatCents(position.avg_price)}</span>
              <span className="polymarket-account-value-cell">
                <strong>{formatCents(position.current_value)}</strong>
                <small>
                  Cost {formatCurrency(positionCost(position))} ·{" "}
                  <span className={pnlClassName(position.cash_pnl)}>
                    {formatSignedCurrency(position.cash_pnl)} {formatSignedPercent(position.percent_pnl)}
                  </span>
                </small>
                {position.redeemable && <span className="polymarket-account-badge">可赎回</span>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AccountOrderSection({
  orders,
  cancelingOrderId,
  onCancelOrder,
}: {
  orders: PolymarketAccountOrder[];
  cancelingOrderId: string | null;
  onCancelOrder: (orderId: string) => void;
}) {
  return (
    <div className="polymarket-account-section">
      <div className="polymarket-account-section-title">当前挂单</div>
      {orders.length === 0 ? (
        <Typography.Text type="secondary">当前 market 暂无挂单</Typography.Text>
      ) : (
        <div className="polymarket-account-table polymarket-order-table">
          <div className="polymarket-account-table-head">
            <span>Side</span>
            <span>Outcome</span>
            <span>Price</span>
            <span>Remaining</span>
            <span>Status</span>
            <span></span>
          </div>
          {orders.map((order) => (
            <div className="polymarket-account-row" key={order.id}>
              <span className="polymarket-account-side">{formatOrderSide(order.side)}</span>
              <span className="polymarket-account-outcome-cell">
                <span className={outcomePillClassName(order.outcome)}>{order.outcome ?? order.asset_id ?? "-"}</span>
              </span>
              <span>{formatCents(order.price)}</span>
              <span>{formatSize(order.remaining_size)}</span>
              <span>{order.status ?? order.order_type ?? "-"}</span>
              <span className="polymarket-account-action-cell">
                <Button
                  danger
                  size="small"
                  loading={cancelingOrderId === order.id}
                  onClick={() => onCancelOrder(order.id)}
                >
                  撤
                </Button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
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

function parsePolymarketAccountMessage(value: string) {
  try {
    const message = JSON.parse(value) as PolymarketAccountStateWsMessage;
    if (message.type !== "polymarket.account_state.snapshot") return null;
    return message;
  } catch {
    return null;
  }
}

function withIndicatorWarmupStart(startMs: number, interval: CandleInterval) {
  // RSI/EMA/BOLL 都由前端计算，历史窗口向前多取一段 K 线作为指标 warmup。
  return Math.max(0, startMs - INDICATOR_WARMUP_BARS * intervalMs(interval));
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

function formatProbability(value: number | null) {
  if (value == null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatCents(value: number | null) {
  if (value == null) return "-";
  return `${Math.round(value * 100)}¢`;
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "操作失败";
}

function formatSize(value: number | null) {
  if (value == null) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatCurrency(value: number | null) {
  if (value == null) return "-";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatSignedCurrency(value: number | null) {
  if (value == null) return "-";
  if (value > 0) return `+${formatCurrency(value)}`;
  if (value < 0) return `-${formatCurrency(Math.abs(value))}`;
  return formatCurrency(value);
}

function formatSignedPercent(value: number | null) {
  if (value == null) return "";
  const percent = Math.abs(value) <= 1 ? value * 100 : value;
  const sign = percent > 0 ? "+" : "";
  return `(${sign}${percent.toLocaleString("en-US", { maximumFractionDigits: 1 })}%)`;
}

function formatCompact(value: number | null) {
  if (value == null) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function positionMatchesMarket(position: PolymarketAccountPosition, market: PolymarketUpDownMarket) {
  if (position.condition_id && market.condition_id && normalizeId(position.condition_id) !== normalizeId(market.condition_id)) {
    return false;
  }
  const asset = position.asset;
  if (!asset) return true;
  return market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(asset));
}

function orderMatchesMarket(order: PolymarketAccountOrder, market: PolymarketUpDownMarket) {
  if (order.market && market.condition_id && normalizeId(order.market) !== normalizeId(market.condition_id)) {
    return false;
  }
  const assetId = order.asset_id;
  if (!assetId) return true;
  return market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(assetId));
}

function tradeMatchesMarket(trade: PolymarketAccountTrade, market: PolymarketUpDownMarket) {
  if (trade.market && market.condition_id && normalizeId(trade.market) !== normalizeId(market.condition_id)) {
    return false;
  }
  const assetId = trade.asset_id;
  if (!assetId) return true;
  return market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(assetId));
}

function normalizeId(value: string) {
  return value.toLowerCase();
}

function positionCost(position: PolymarketAccountPosition) {
  if (position.avg_price == null || position.size == null) return null;
  return position.avg_price * position.size;
}

function accountStateStatusLabel(state: string) {
  if (state === "running") return "User WS 已连接";
  if (state === "connecting") return "User WS 连接中";
  if (state === "reconnecting") return "User WS 重连中";
  if (state === "config_missing") return "User WS 未配置";
  if (state === "stopped") return "User WS 已停止";
  return "REST 快照";
}

function pnlClassName(value: number | null) {
  if (value == null || value === 0) return "polymarket-account-pnl neutral";
  return value > 0 ? "polymarket-account-pnl positive" : "polymarket-account-pnl negative";
}

function outcomePillClassName(outcome: string | null) {
  const normalized = (outcome ?? "").toLowerCase();
  if (normalized === "up" || normalized === "yes") return "polymarket-account-outcome-pill up";
  if (normalized === "down" || normalized === "no") return "polymarket-account-outcome-pill down";
  return "polymarket-account-outcome-pill";
}

function formatOrderSide(side: string | null) {
  const normalized = (side ?? "").toLowerCase();
  if (normalized === "buy") return "BUY";
  if (normalized === "sell") return "SELL";
  return side ?? "-";
}

function tradeStatusLabel(status: PolymarketAccountTrade["confirmation_status"]) {
  if (status === "pending") return "待确认";
  if (status === "refresh_failed") return "刷新失败";
  return "已确认";
}

function tradeStatusClassName(status: PolymarketAccountTrade["confirmation_status"]) {
  return `polymarket-account-trade-status ${status}`;
}

function formatTradeTime(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBtcPrice(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatMarketPriceDiff(value: number) {
  const abs = Math.abs(value);
  const maximumFractionDigits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return abs.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  });
}

function formatDateTimeLocalInput(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function parseDateTimeLocalInput(value: string) {
  if (!value.trim()) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
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

function formatMarketEndTime(market: PolymarketUpDownMarket) {
  const window = polymarketDisplayWindow(market);
  if (!window) return market.window;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(window.endMs));
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

function sortMarketsForRail(markets: PolymarketUpDownMarket[]) {
  return [...markets].sort((left, right) => marketWindowStartMs(left) - marketWindowStartMs(right));
}

function buildMarketRailModel(
  markets: PolymarketUpDownMarket[],
  activeMarketId: string | undefined,
  selectedMarketId: string | null
) {
  const sortedMarkets = sortMarketsForRail(markets);
  const currentMarket = sortedMarkets.find((market) => marketWindowLabel(market, sortedMarkets) === "当前");
  const currentMarketId = currentMarket?.id;
  const selectedMarket = selectedMarketId
    ? sortedMarkets.find((market) => market.id === selectedMarketId)
    : undefined;

  const pastMarkets = sortedMarkets.filter((market) => marketWindowLabel(market, sortedMarkets) === "已结束");
  const latestPastMarket = pastMarkets.at(-1);
  const futureMarkets = sortedMarkets.filter(
    (market) =>
      market.id !== currentMarketId &&
      marketWindowLabel(market, sortedMarkets) !== "已结束"
  );

  const visibleMarkets: PolymarketUpDownMarket[] = [];
  const appendUnique = (market: PolymarketUpDownMarket | undefined) => {
    if (!market) return;
    if (latestPastMarket && market.id === latestPastMarket.id) return;
    if (visibleMarkets.some((item) => item.id === market.id)) return;
    visibleMarkets.push(market);
  };

  appendUnique(currentMarket ?? sortedMarkets.find((market) => market.id === activeMarketId) ?? sortedMarkets[0]);
  if (selectedMarket && selectedMarket.id !== currentMarketId) appendUnique(selectedMarket);

  for (const market of futureMarkets) {
    if (visibleMarkets.length >= MAX_VISIBLE_MARKET_PILLS) break;
    appendUnique(market);
  }

  appendUnique(currentMarket ?? sortedMarkets.find((market) => market.id === activeMarketId) ?? sortedMarkets[0]);
  if (selectedMarket && selectedMarket.id !== currentMarketId) appendUnique(selectedMarket);

  visibleMarkets.sort((left, right) => marketWindowStartMs(left) - marketWindowStartMs(right));

  const visibleIds = new Set(visibleMarkets.map((market) => market.id));
  const moreMarkets = futureMarkets.filter((market) => !visibleIds.has(market.id));

  return {
    latestPastMarket,
    visibleMarkets,
    moreMarkets,
  };
}

function marketWindowStartMs(market: PolymarketUpDownMarket) {
  return polymarketDisplayWindow(market)?.startMs ?? Number.POSITIVE_INFINITY;
}
