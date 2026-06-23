import { ClockCircleOutlined, DownOutlined, ExportOutlined, FullscreenExitOutlined, FullscreenOutlined } from "@ant-design/icons";
import { Button, Card, Checkbox, Dropdown, Empty, InputNumber, Modal, Segmented, Typography, notification } from "antd";
import type { MenuProps } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClobClient, OrderType, Side } from "@polymarket/clob-client-v2";
import { createWalletClient, custom, type Address } from "viem";
import { polygon } from "viem/chains";
import {
  api,
  type CandleInterval,
  type PolymarketAccountOrder,
  type PolymarketAccountPosition,
  type PolymarketAccountState,
  type PolymarketAccountTrade,
  type PolymarketCredentialProfile,
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
import { useWalletConnection, type EthereumProvider } from "../hooks/useWalletConnection";
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
const POLYGON_CHAIN_ID = "0x89";
const POLYMARKET_CLOB_HOST = "https://clob.polymarket.com";
const POLYMARKET_ORDERBOOK_VISIBLE_ROWS = 4;
const DEFAULT_ORDER_AMOUNT = 5;

type TradeDraft = {
  marketId: string;
  tokenId: string;
  side: "BUY" | "SELL";
  nonce: number;
};
// React Query 加载期需要稳定空数组，避免 effect 依赖因内联 [] 新引用反复触发 setState。
const EMPTY_MARKET_CANDLES: MarketCandle[] = [];
const EMPTY_POLYMARKET_MARKETS: PolymarketUpDownMarket[] = [];
const EMPTY_ACCOUNT_STATE: PolymarketAccountState = {
  wallet: null,
  clob_address: null,
  balance: null,
  trading_restriction: null,
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
  const walletConnection = useWalletConnection();
  const walletConnected = Boolean(walletConnection.address);
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
  const { data: credentialData } = useQuery({
    queryKey: ["polymarket-credentials"],
    queryFn: api.polymarketCredentials,
    enabled: walletConnected,
    refetchOnWindowFocus: false,
  });
  const activeCredential = useMemo(
    () => credentialData?.profiles.find((profile) => profile.id === credentialData.active_id) ?? null,
    [credentialData],
  );
  const activeCredentialMatches = Boolean(
    walletConnection.address &&
      activeCredential &&
      normalizeId(activeCredential.signer_address) === normalizeId(walletConnection.address),
  );
  const activeCredentialQueryId = activeCredential?.id ?? "none";
  const {
    data: accountStateSnapshot = EMPTY_ACCOUNT_STATE,
    error: accountStateError,
  } = useQuery({
    queryKey: ["polymarket-account-state", "global", activeCredentialQueryId],
    queryFn: () => api.polymarketAccountState(),
    enabled: activeCredentialMatches && Boolean(selectedPolymarketConditionId),
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
  const candleSnapshotModeRef = useRef<"latest" | "focus">(candleSnapshotQuery.mode);
  const { data: latestCandles = EMPTY_MARKET_CANDLES, dataUpdatedAt: latestCandlesUpdatedAt, error } = useQuery({
    queryKey: candleSnapshotQuery.queryKey,
    queryFn: ({ signal }) => candleSnapshotQuery.queryFn(signal),
    enabled: true,
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
    candleSnapshotModeRef.current = candleSnapshotQuery.mode;
  }, [candleSnapshotQuery.mode]);

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
    if (!activeCredentialMatches || !selectedPolymarketConditionId) {
      setPolymarketAccountState(EMPTY_ACCOUNT_STATE);
      return;
    }
    setPolymarketAccountState(accountStateSnapshot);
  }, [accountStateSnapshot, activeCredentialMatches, activeCredentialQueryId, selectedPolymarketConditionId]);

  useEffect(() => {
    if (!selectedPolymarketId) {
      setSelectedPolymarketSnapshot(null);
      return;
    }
    const freshMarket = polymarketMarkets.find((market) => market.id === selectedPolymarketId);
    if (freshMarket) setSelectedPolymarketSnapshot(freshMarket);
  }, [polymarketMarkets, selectedPolymarketId]);

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
        if (candle) {
          if (!candleSnapshotReadyRef.current && candleSnapshotModeRef.current === "focus") {
            // focus 模式必须先等 REST 窗口确定定位；latest 模式则允许 WS 未收盘 K 线立即上屏。
            pendingLiveCandlesRef.current = mergeCandles(pendingLiveCandlesRef.current, [candle]);
            return;
          }
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
          walletConnected={walletConnected}
          walletProfileReady={activeCredentialMatches}
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
  walletConnected,
  walletProfileReady,
}: {
  interval: PolymarketInterval;
  markets: PolymarketUpDownMarket[];
  selectedMarket: PolymarketUpDownMarket | undefined;
  selectedMarketId: string | null;
  onSelectedMarketId: (marketId: string, followCurrent?: boolean) => void;
  error: string | null;
  accountState: PolymarketAccountState;
  accountStateError: string | null;
  walletConnected: boolean;
  walletProfileReady: boolean;
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
  const [tradeDraft, setTradeDraft] = useState<TradeDraft | null>(null);
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
              <OutcomeQuoteCard
                key={`${activeMarket.id}:${quote.name}`}
                marketId={activeMarket.id}
                quote={quote}
                onStartTrade={setTradeDraft}
              />
            ))}
          </div>
          <AccountStatePanel
            market={activeMarket}
            tradeDraft={tradeDraft}
            onCloseTrade={() => setTradeDraft(null)}
            accountState={accountState}
            error={accountStateError}
            walletConnected={walletConnected}
            walletProfileReady={walletProfileReady}
          />
        </div>
      )}
    </Card>
  );
}

function AccountStatePanel({
  market,
  tradeDraft,
  onCloseTrade,
  accountState,
  error,
  walletConnected,
  walletProfileReady,
}: {
  market: PolymarketUpDownMarket;
  tradeDraft: TradeDraft | null;
  onCloseTrade: () => void;
  accountState: PolymarketAccountState;
  error: string | null;
  walletConnected: boolean;
  walletProfileReady: boolean;
}) {
  const positions = accountState.positions.filter((position) => positionMatchesMarket(position, market));
  const orders = accountState.orders.filter((order) => orderMatchesMarket(order, market));
  const trades = accountState.recent_trades.filter((trade) => tradeMatchesMarket(trade, market));
  const wsLabel = accountStateStatusLabel(accountState.ws_state);
  const queryClient = useQueryClient();
  const [cancelingOrderId, setCancelingOrderId] = useState<string | null>(null);
  const [notificationApi, notificationContextHolder] = notification.useNotification();
  const showTradeNotice = useCallback(
    (type: "success" | "error" | "warning", message: string, description?: string) => {
      const options = {
        message,
        description,
        placement: "topRight" as const,
        duration: 4,
      };
      if (type === "success") notificationApi.success(options);
      if (type === "error") notificationApi.error(options);
      if (type === "warning") notificationApi.warning(options);
    },
    [notificationApi],
  );
  const credentialsQuery = useQuery({
    queryKey: ["polymarket-credentials"],
    queryFn: api.polymarketCredentials,
    enabled: walletConnected,
    refetchOnWindowFocus: false,
  });
  const activeCredential =
    credentialsQuery.data?.profiles.find((profile) => profile.id === credentialsQuery.data?.active_id) ?? null;
  const accountStateQueryKey = ["polymarket-account-state", "global", activeCredential?.id ?? "none"];
  const tradeModalOpen = Boolean(tradeDraft?.marketId === market.id);
  const tradeDraftQuote = tradeDraft
    ? market.outcome_quotes.find((quote) => quote.token_id === tradeDraft.tokenId)
    : null;
  const tradeModalTone = tradeDraftQuote?.name.toLowerCase() === "down" ? "down" : "up";
  const cancelOrderMutation = useMutation({
    mutationFn: api.cancelPolymarketOrder,
    onMutate: (orderId: string) => {
      setCancelingOrderId(orderId);
    },
    onSuccess: () => {
      showTradeNotice("success", "撤单已提交");
      queryClient.invalidateQueries({ queryKey: accountStateQueryKey });
    },
    onError: (mutationError) => {
      showTradeNotice("error", "撤单失败", errorMessage(mutationError));
    },
    onSettled: () => {
      setCancelingOrderId(null);
    },
  });
  const tradeModal = (
    <Modal
      className={`polymarket-trade-modal ${tradeModalTone}`}
      open={tradeModalOpen}
      footer={null}
      destroyOnHidden
      onCancel={onCloseTrade}
    >
      <PolymarketOrderEntry
        market={market}
        tradeDraft={tradeDraft}
        positions={positions}
        tradingRestriction={accountState.trading_restriction}
        activeCredential={activeCredential}
        onNotice={showTradeNotice}
        onOrderSubmitted={() => {
          queryClient.invalidateQueries({ queryKey: accountStateQueryKey });
          queryClient.invalidateQueries({ queryKey: ["polymarket-account-state"] });
          onCloseTrade();
        }}
      />
    </Modal>
  );
  if (!walletConnected) {
    return (
      <div className="polymarket-account-panel polymarket-account-panel-guest">
        {notificationContextHolder}
        {tradeModal}
        <div className="polymarket-account-head">
          <Typography.Text strong>游客模式</Typography.Text>
          <Typography.Text type="secondary">未连接钱包</Typography.Text>
        </div>
        <Typography.Text type="secondary">连接 MetaMask 后显示当前 market 的仓位、挂单和下单入口。</Typography.Text>
      </div>
    );
  }
  if (!walletProfileReady) {
    return (
      <div className="polymarket-account-panel polymarket-account-panel-guest">
        {notificationContextHolder}
        {tradeModal}
        <div className="polymarket-account-head">
          <Typography.Text strong>钱包已连接</Typography.Text>
          <Typography.Text type="secondary">未启用 profile</Typography.Text>
        </div>
        <Typography.Text type="secondary">请点击右上角账户区域初始化或切换到当前 MetaMask 匹配的 wallet profile。</Typography.Text>
      </div>
    );
  }
  return (
    <div className="polymarket-account-panel">
      {notificationContextHolder}
      <div className="polymarket-account-head">
        <Typography.Text strong>我的账户</Typography.Text>
        <Typography.Text type="secondary">{wsLabel}</Typography.Text>
      </div>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
      {accountState.error && <Typography.Text type="secondary">{accountStateErrorSummary(accountState.error)}</Typography.Text>}
      {tradeModal}
      <AccountPositionSection market={market} positions={positions} trades={trades} />
      <AccountOrderSection
        orders={orders}
        cancelingOrderId={cancelingOrderId}
        onCancelOrder={(orderId) => cancelOrderMutation.mutate(orderId)}
      />
      <AccountTradeSection trades={trades} />
    </div>
  );
}

function PolymarketOrderEntry({
  market,
  tradeDraft,
  positions,
  tradingRestriction,
  activeCredential,
  onNotice,
  onOrderSubmitted,
}: {
  market: PolymarketUpDownMarket;
  tradeDraft: TradeDraft | null;
  positions: PolymarketAccountPosition[];
  tradingRestriction: PolymarketAccountState["trading_restriction"];
  activeCredential: PolymarketCredentialProfile | null;
  onNotice: (type: "success" | "error" | "warning", message: string, description?: string) => void;
  onOrderSubmitted: () => void;
}) {
  const firstTokenId = market.outcome_quotes.find((quote) => quote.token_id)?.token_id ?? null;
  const [tokenId, setTokenId] = useState<string | null>(firstTokenId);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [orderMode, setOrderMode] = useState<"MARKET" | "LIMIT">("MARKET");
  const [priceCents, setPriceCents] = useState<number | null>(() => defaultOrderPriceCents(market.outcome_quotes[0], "BUY"));
  const [amount, setAmount] = useState<number | null>(DEFAULT_ORDER_AMOUNT);
  const [submitting, setSubmitting] = useState(false);
  const selectedQuote = market.outcome_quotes.find((quote) => quote.token_id === tokenId) ?? market.outcome_quotes[0] ?? null;
  const selectedOutcome = selectedQuote?.name ?? "-";
  const closeOnly = Boolean(tradingRestriction?.close_only);
  const selectedPositionSize = selectedQuote?.token_id ? positionSizeForToken(positions, selectedQuote.token_id) : 0;
  const marketPrice = marketOrderPrice(selectedQuote, side);
  const orderEstimate = useMemo(
    () =>
      estimateOrderSummary({
        quote: selectedQuote,
        side,
        orderMode,
        amount,
        priceCents,
        marketPrice,
      }),
    [amount, marketPrice, orderMode, priceCents, selectedQuote, side],
  );
  const inputLabel = orderMode === "MARKET" && side === "BUY" ? "Amount" : "Shares";
  const inputSuffix = orderMode === "MARKET" && side === "BUY" ? "USDC" : "shares";
  const maxInput = side === "SELL" ? selectedPositionSize : undefined;
  const sideOptions = closeOnly
    ? [{ label: "Sell", value: "SELL" }]
    : [
        { label: "Buy", value: "BUY" },
        { label: "Sell", value: "SELL" },
      ];
  const canSubmit = Boolean(
    activeCredential &&
      market.accepting_orders &&
      selectedQuote?.token_id &&
      amount &&
      amount >= 1 &&
      (orderMode === "MARKET" || priceCents) &&
      (!closeOnly || side === "SELL") &&
      (side !== "SELL" || amount <= selectedPositionSize),
  );

  useEffect(() => {
    const nextTokenId = market.outcome_quotes.find((quote) => quote.token_id)?.token_id ?? null;
    setTokenId(nextTokenId);
  }, [market.id]);

  useEffect(() => {
    if (!tradeDraft || tradeDraft.marketId !== market.id) return;
    setTokenId(tradeDraft.tokenId);
    setSide(tradeDraft.side);
    setOrderMode("MARKET");
    const quote = market.outcome_quotes.find((item) => item.token_id === tradeDraft.tokenId);
    setPriceCents(defaultOrderPriceCents(quote, tradeDraft.side));
    setAmount(DEFAULT_ORDER_AMOUNT);
  }, [market.id, tradeDraft?.nonce]);

  useEffect(() => {
    setPriceCents(defaultOrderPriceCents(selectedQuote, side));
  }, [selectedQuote?.token_id, side]);

  useEffect(() => {
    if (closeOnly && side === "BUY") setSide("SELL");
  }, [closeOnly, side]);

  const handleSubmit = async () => {
    if (!activeCredential) {
      onNotice("warning", "无法下单", "请先点击右上角账户区域连接 MetaMask，并启用匹配的 wallet profile");
      return;
    }
    if (!market.accepting_orders) {
      onNotice("warning", "无法下单", "当前 market 暂停接单");
      return;
    }
    if (!selectedQuote?.token_id) {
      onNotice("warning", "无法下单", "当前 outcome 缺少 token_id，无法下单");
      return;
    }
    if (orderMode === "LIMIT" && (!priceCents || priceCents <= 0 || priceCents >= 100)) {
      onNotice("warning", "请输入有效限价", "请输入 1-99¢ 的限价");
      return;
    }
    if (!amount || amount < 1) {
      onNotice("warning", "请输入数量", side === "BUY" && orderMode === "MARKET" ? "请输入买入金额" : "请输入 shares 数量");
      return;
    }
    if (closeOnly && side === "BUY") {
      onNotice("warning", "当前地区为 close-only", "只允许 SELL 平仓");
      return;
    }
    if (side === "SELL" && amount > selectedPositionSize) {
      onNotice("warning", "SELL size 超出持仓", `不能超过当前持仓 ${formatSize(selectedPositionSize)}`);
      return;
    }
    setSubmitting(true);
    try {
      const provider = requireEthereumProvider();
      const accounts = await provider.request<string[]>({ method: "eth_requestAccounts" });
      const signerAddress = accounts[0];
      if (!signerAddress) throw new Error("MetaMask 未返回钱包地址");
      if (normalizeId(signerAddress) !== normalizeId(activeCredential.signer_address)) {
        throw new Error(`MetaMask 当前地址不是 active signer: ${shortAddress(activeCredential.signer_address)}`);
      }
      await switchToPolygon(provider);
      const walletClient = createWalletClient({
        account: signerAddress as Address,
        chain: polygon,
        transport: custom(provider),
      });
      // BUY/SELL 订单只在 MetaMask 内签名；前端只把一次性的 signed order 发给后端提交。
      const clobClient = new ClobClient({
        host: POLYMARKET_CLOB_HOST,
        chain: 137,
        signer: walletClient,
        signatureType: activeCredential.signature_type,
        funderAddress: activeCredential.funder_address,
      });
      const price = orderMode === "LIMIT" ? (priceCents ?? 0) / 100 : marketPrice ?? selectedQuote.price ?? selectedQuote.last_trade_price ?? 0;
      const signedOrder =
        orderMode === "MARKET"
          ? await clobClient.createMarketOrder(
              {
                tokenID: selectedQuote.token_id,
                amount,
                side: side === "BUY" ? Side.BUY : Side.SELL,
                price: marketPrice ?? undefined,
                orderType: OrderType.FOK,
              },
              { tickSize: "0.01" },
            )
          : await clobClient.createOrder(
              {
                tokenID: selectedQuote.token_id,
                price,
                side: side === "BUY" ? Side.BUY : Side.SELL,
                size: amount,
              },
              { tickSize: "0.01" },
            );
      const response = await api.postSignedPolymarketOrder({
        signed_order: signedOrder as unknown as Record<string, unknown>,
        condition_id: market.condition_id,
        token_id: selectedQuote.token_id,
        side,
        price,
        size: amount,
        order_type: orderMode === "MARKET" ? "FOK" : "GTC",
        post_only: orderMode === "LIMIT",
        defer_exec: false,
      });
      onNotice(
        "success",
        "下单已提交",
        response.order_id
          ? `${orderMode === "MARKET" ? "市价单" : "挂单"}已提交：${shortAddress(response.order_id)}`
          : `${orderMode === "MARKET" ? "市价单" : "挂单"}已提交`,
      );
      onOrderSubmitted();
    } catch (error) {
      onNotice("error", "下单失败", errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="polymarket-account-section">
      <div className={`polymarket-order-entry ${side.toLowerCase()} ${selectedOutcome.toLowerCase()}`}>
        <div className="polymarket-order-entry-head">
          <div>
            <span>交易</span>
            <small>{market.accepting_orders ? "当前 market 可交易" : "暂停接单"}</small>
          </div>
          <strong>{selectedOutcome} {formatCents(marketPrice)}</strong>
        </div>
        <div className="polymarket-order-entry-controls">
          <Segmented
            size="small"
            value={side}
            options={sideOptions}
            onChange={(value) => setSide(value as "BUY" | "SELL")}
          />
          <Segmented
            size="small"
            value={orderMode}
            options={[
              { label: "Market", value: "MARKET" },
              { label: "Limit", value: "LIMIT" },
            ]}
            onChange={(value) => setOrderMode(value as "MARKET" | "LIMIT")}
          />
        </div>
        <div className="polymarket-outcome-selector">
          <Segmented
            size="small"
            value={tokenId ?? undefined}
            options={market.outcome_quotes.map((quote) => ({ label: quote.name, value: quote.token_id ?? quote.name }))}
            onChange={(value) => setTokenId(String(value))}
          />
          <Typography.Text type="secondary">
            {selectedOutcome} {formatCents(marketPrice)}
          </Typography.Text>
        </div>
        <div className="polymarket-order-entry-inputs">
          {orderMode === "LIMIT" && (
            <label>
              Price
              <InputNumber
                min={1}
                max={99}
                step={1}
                value={priceCents}
                addonAfter="¢"
                onChange={(value) => setPriceCents(typeof value === "number" ? value : null)}
              />
            </label>
          )}
          <label>
            {inputLabel}
            <InputNumber
              min={1}
              max={maxInput}
              step={1}
              value={amount}
              addonAfter={inputSuffix}
              placeholder={side === "BUY" && orderMode === "MARKET" ? "USDC" : "shares"}
              onChange={(value) => setAmount(typeof value === "number" ? value : null)}
            />
          </label>
        </div>
        {side === "SELL" && (
          <div className="polymarket-order-entry-presets">
            {[0.25, 0.5, 0.75, 1].map((ratio) => (
              <Button key={ratio} size="small" onClick={() => setAmount(roundInputAmount(selectedPositionSize * ratio))}>
                {ratio === 1 ? "Max" : `${Math.round(ratio * 100)}%`}
              </Button>
            ))}
          </div>
        )}
        {side === "BUY" && orderMode === "MARKET" && (
          <div className="polymarket-order-entry-presets">
            {[1, 5, 10, 100].map((preset) => (
              <Button key={preset} size="small" onClick={() => setAmount(roundInputAmount((amount ?? 0) + preset))}>
                +${preset}
              </Button>
            ))}
          </div>
        )}
        <div className="polymarket-order-entry-estimate">
          <div>
            <span>{side === "BUY" ? "Total" : "Receive"}</span>
            <strong>{formatCurrency(orderEstimate.total)}</strong>
          </div>
          <div>
            <span>To win</span>
            <strong className="polymarket-order-entry-win">{formatCurrency(orderEstimate.toWin)}</strong>
          </div>
          <small>
            Avg. Price {formatCents(orderEstimate.avgPrice)}
            {orderEstimate.depthLimited ? " · 深度不足，按当前盘口估算" : ""}
          </small>
        </div>
        <div className="polymarket-order-entry-submit">
          <div className="polymarket-order-entry-meta">
            <Checkbox checked={orderMode === "LIMIT"} disabled>
              {orderMode === "LIMIT" ? "Post only · GTC" : "FOK · immediate"}
            </Checkbox>
            <span>
              {selectedOutcome} / {side} / {orderMode === "MARKET" ? "Market" : priceCents ? `${priceCents}¢` : "-"} /{" "}
              {amount ? formatSize(amount) : "-"} {inputSuffix}
              {side === "SELL" ? ` / max ${formatSize(selectedPositionSize)}` : ""}
            </span>
          </div>
          <Button type="primary" size="small" loading={submitting} disabled={!canSubmit} onClick={handleSubmit}>
            {orderMode === "MARKET" ? (side === "BUY" ? "市价买入" : "市价卖出") : "挂单"}
          </Button>
        </div>
        {!activeCredential && (
          <Typography.Text type="secondary">需要先在右上角账户区域连接 MetaMask，并启用匹配的 wallet profile。</Typography.Text>
        )}
        {closeOnly && (
          <Typography.Text type="secondary">
            当前地区 {tradingRestriction?.country ?? ""} 为 close-only：BUY 已禁用，只能 SELL 已有 shares。
          </Typography.Text>
        )}
      </div>
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

function AccountPositionSection({
  market,
  positions,
  trades,
}: {
  market: PolymarketUpDownMarket;
  positions: PolymarketAccountPosition[];
  trades: PolymarketAccountTrade[];
}) {
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
            <span>Return</span>
          </div>
          {positions.map((position) => {
            const metrics = positionDisplayMetrics(position, market, trades);
            return (
              <div className="polymarket-account-row" key={`${position.condition_id}:${position.asset}:${position.outcome}`}>
                <span className="polymarket-account-outcome-cell">
                  <span className={outcomePillClassName(position.outcome)}>{position.outcome ?? position.asset ?? "-"}</span>
                </span>
                <span>{formatSize(position.size)}</span>
                <span>{formatCents(metrics.avgPrice)}</span>
                <span className="polymarket-account-value-cell">
                  <strong>{formatCurrency(metrics.currentValue)}</strong>
                  <small>Cost {formatCurrency(metrics.cost)}</small>
                  {position.redeemable && <span className="polymarket-account-badge">可赎回</span>}
                </span>
                <span className={`polymarket-account-return-cell ${pnlClassName(metrics.pnl)}`}>
                  <strong>{formatSignedCurrency(metrics.pnl)}</strong>
                  <small>{formatSignedPercent(metrics.percentPnl)}</small>
                </span>
              </div>
            );
          })}
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

function OutcomeQuoteCard({
  marketId,
  quote,
  onStartTrade,
}: {
  marketId: string;
  quote: PolymarketOutcomeQuote;
  onStartTrade: (draft: TradeDraft) => void;
}) {
  const displayPrice = formatCents(quote.buy_price ?? quote.best_ask ?? quote.price);
  const canStartTrade = Boolean(quote.token_id);
  const outcomeTone = quote.name.toLowerCase() === "up" ? "up" : "down";
  return (
    <button
      type="button"
      className={`polymarket-outcome ${outcomeTone}`}
      disabled={!canStartTrade}
      onClick={() => {
        if (!quote.token_id) return;
        onStartTrade({
          marketId,
          tokenId: quote.token_id,
          side: "BUY",
          nonce: Date.now(),
        });
      }}
      aria-label={`交易 ${quote.name}`}
      data-testid={`polymarket-trade-${outcomeTone}`}
    >
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
    </button>
  );
}

function OrderBook({ quote }: { quote: PolymarketOutcomeQuote }) {
  const depth = Math.max(quote.bids.length, quote.asks.length);
  if (depth === 0) {
    return <Typography.Text type="secondary">暂无订单簿</Typography.Text>;
  }
  const visibleDepth = Math.min(depth, POLYMARKET_ORDERBOOK_VISIBLE_ROWS);
  return (
    <div className="polymarket-orderbook">
      <div className="polymarket-orderbook-head">
        <span>Bid</span>
        <span>Size</span>
        <span>Ask</span>
        <span>Size</span>
      </div>
      {Array.from({ length: visibleDepth }).map((_, index) => {
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

function requireEthereumProvider() {
  if (!window.ethereum) {
    throw new Error("未检测到 MetaMask，请先安装或打开钱包插件");
  }
  return window.ethereum;
}

async function switchToPolygon(provider: EthereumProvider) {
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: POLYGON_CHAIN_ID }] });
  } catch (error) {
    const code = typeof error === "object" && error !== null && "code" in error ? (error as { code?: number }).code : null;
    if (code !== 4902) throw error;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: POLYGON_CHAIN_ID,
          chainName: "Polygon",
          nativeCurrency: { name: "POL", symbol: "POL", decimals: 18 },
          rpcUrls: ["https://polygon-rpc.com"],
          blockExplorerUrls: ["https://polygonscan.com"],
        },
      ],
    });
  }
}

function defaultOrderPriceCents(quote: PolymarketOutcomeQuote | null | undefined, side: "BUY" | "SELL") {
  if (!quote) return null;
  const value = side === "BUY" ? quote.best_bid ?? quote.sell_price ?? quote.price : quote.best_ask ?? quote.buy_price ?? quote.price;
  return value == null ? null : Math.round(value * 100);
}

function marketOrderPrice(quote: PolymarketOutcomeQuote | null | undefined, side: "BUY" | "SELL") {
  if (!quote) return null;
  return side === "BUY"
    ? quote.buy_price ?? quote.best_ask ?? quote.price ?? quote.last_trade_price
    : quote.sell_price ?? quote.best_bid ?? quote.price ?? quote.last_trade_price;
}

function estimateOrderSummary({
  quote,
  side,
  orderMode,
  amount,
  priceCents,
  marketPrice,
}: {
  quote: PolymarketOutcomeQuote | null;
  side: "BUY" | "SELL";
  orderMode: "MARKET" | "LIMIT";
  amount: number | null;
  priceCents: number | null;
  marketPrice: number | null;
}) {
  if (!amount || amount < 1) return { total: 0, toWin: 0, avgPrice: null, depthLimited: false };
  const limitPrice = priceCents ? priceCents / 100 : null;
  if (orderMode === "LIMIT") {
    if (!limitPrice || limitPrice <= 0) return { total: 0, toWin: 0, avgPrice: null, depthLimited: false };
    const grossPayout = side === "BUY" ? amount : 0;
    return {
      total: amount * limitPrice,
      toWin: grossPayout,
      avgPrice: limitPrice,
      depthLimited: false,
    };
  }
  if (!quote) return { total: side === "BUY" ? amount : 0, toWin: 0, avgPrice: marketPrice, depthLimited: false };
  return side === "BUY"
    ? estimateMarketBuy(amount, quote, marketPrice)
    : estimateMarketSell(amount, quote, marketPrice);
}

function estimateMarketBuy(cashAmount: number, quote: PolymarketOutcomeQuote, fallbackPrice: number | null) {
  let remainingCash = cashAmount;
  let shares = 0;
  let spent = 0;
  const asks = [...quote.asks]
    .filter((level) => level.price != null && level.price > 0 && level.size != null && level.size > 0)
    .sort((left, right) => (left.price ?? 0) - (right.price ?? 0));
  for (const level of asks) {
    if (remainingCash <= 0) break;
    const price = level.price ?? 0;
    const size = level.size ?? 0;
    const maxCost = price * size;
    const usedCash = Math.min(remainingCash, maxCost);
    shares += usedCash / price;
    spent += usedCash;
    remainingCash -= usedCash;
  }
  if (remainingCash > 0 && fallbackPrice && fallbackPrice > 0) {
    shares += remainingCash / fallbackPrice;
    spent += remainingCash;
    remainingCash = 0;
  }
  return {
    total: spent,
    toWin: shares,
    avgPrice: shares > 0 ? spent / shares : fallbackPrice,
    depthLimited: remainingCash > 0,
  };
}

function estimateMarketSell(shareAmount: number, quote: PolymarketOutcomeQuote, fallbackPrice: number | null) {
  let remainingShares = shareAmount;
  let soldShares = 0;
  let proceeds = 0;
  const bids = [...quote.bids]
    .filter((level) => level.price != null && level.price > 0 && level.size != null && level.size > 0)
    .sort((left, right) => (right.price ?? 0) - (left.price ?? 0));
  for (const level of bids) {
    if (remainingShares <= 0) break;
    const price = level.price ?? 0;
    const size = level.size ?? 0;
    const usedShares = Math.min(remainingShares, size);
    proceeds += usedShares * price;
    soldShares += usedShares;
    remainingShares -= usedShares;
  }
  if (remainingShares > 0 && fallbackPrice && fallbackPrice > 0) {
    proceeds += remainingShares * fallbackPrice;
    soldShares += remainingShares;
    remainingShares = 0;
  }
  return {
    total: proceeds,
    toWin: 0,
    avgPrice: soldShares > 0 ? proceeds / soldShares : fallbackPrice,
    depthLimited: remainingShares > 0,
  };
}

function roundInputAmount(value: number) {
  return Math.max(0, Math.round(value * 100) / 100);
}

function positionSizeForToken(positions: PolymarketAccountPosition[], tokenId: string) {
  return positions.reduce((total, position) => {
    if (!position.asset || normalizeId(position.asset) !== normalizeId(tokenId)) return total;
    return total + (position.size ?? 0);
  }, 0);
}

function shortAddress(value: string) {
  if (value.length <= 14) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
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
  const asset = position.asset;
  if (asset && market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(asset))) {
    return true;
  }
  return Boolean(
    position.condition_id &&
      market.condition_id &&
      normalizeId(position.condition_id) === normalizeId(market.condition_id),
  );
}

function orderMatchesMarket(order: PolymarketAccountOrder, market: PolymarketUpDownMarket) {
  const assetId = order.asset_id;
  if (assetId && market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(assetId))) {
    return true;
  }
  return Boolean(
    order.market &&
      market.condition_id &&
      normalizeId(order.market) === normalizeId(market.condition_id),
  );
}

function tradeMatchesMarket(trade: PolymarketAccountTrade, market: PolymarketUpDownMarket) {
  const assetId = trade.asset_id;
  if (assetId && market.outcome_quotes.some((quote) => quote.token_id && normalizeId(quote.token_id) === normalizeId(assetId))) {
    return true;
  }
  return Boolean(
    trade.market &&
      market.condition_id &&
      normalizeId(trade.market) === normalizeId(market.condition_id),
  );
}

function normalizeId(value: string) {
  return value.toLowerCase();
}

function positionCost(position: PolymarketAccountPosition) {
  if (position.avg_price == null || position.size == null) return null;
  return position.avg_price * position.size;
}

function positionDisplayMetrics(
  position: PolymarketAccountPosition,
  market: PolymarketUpDownMarket,
  trades: PolymarketAccountTrade[],
) {
  const tradeBasis = positionTradeBasis(position, trades);
  const avgPrice = position.avg_price && position.avg_price > 0 ? position.avg_price : tradeBasis?.avgPrice ?? null;
  const livePrice = positionLivePrice(position, market);
  const currentValue =
    livePrice != null && position.size != null
      ? livePrice * position.size
      : position.current_value;
  const cost =
    avgPrice != null && position.size != null
      ? avgPrice * position.size
      : tradeBasis?.cost ?? positionCost(position);
  const pnl =
    cost != null && currentValue != null
      ? currentValue - cost
      : position.cash_pnl;
  const percentPnl = cost != null && cost > 0 && pnl != null ? pnl / cost : position.percent_pnl;
  return { avgPrice, cost, currentValue, pnl, percentPnl };
}

function positionLivePrice(position: PolymarketAccountPosition, market: PolymarketUpDownMarket) {
  const quote = market.outcome_quotes.find((item) => quoteMatchesPosition(item, position));
  if (!quote) return position.cur_price;
  return quote.sell_price ?? quote.best_bid ?? quote.last_trade_price ?? quote.price ?? position.cur_price;
}

function positionTradeBasis(position: PolymarketAccountPosition, trades: PolymarketAccountTrade[]) {
  const matchingBuys = trades.filter((trade) => {
    if ((trade.side ?? "").toUpperCase() !== "BUY") return false;
    if (trade.price == null || trade.size == null || trade.size <= 0) return false;
    return tradeMatchesPosition(trade, position);
  });
  if (matchingBuys.length === 0) return null;
  const totalSize = matchingBuys.reduce((sum, trade) => sum + (trade.size ?? 0), 0);
  if (totalSize <= 0) return null;
  const totalCost = matchingBuys.reduce((sum, trade) => sum + (trade.price ?? 0) * (trade.size ?? 0), 0);
  const avgPrice = totalCost / totalSize;
  const cost = position.size != null && position.size > 0 ? avgPrice * position.size : totalCost;
  return { avgPrice, cost };
}

function tradeMatchesPosition(trade: PolymarketAccountTrade, position: PolymarketAccountPosition) {
  if (trade.asset_id && position.asset && normalizeId(trade.asset_id) === normalizeId(position.asset)) {
    return true;
  }
  const tradeOutcome = (trade.outcome ?? "").toLowerCase();
  const positionOutcome = (position.outcome ?? "").toLowerCase();
  return Boolean(tradeOutcome && positionOutcome && tradeOutcome === positionOutcome);
}

function quoteMatchesPosition(quote: PolymarketOutcomeQuote, position: PolymarketAccountPosition) {
  if (quote.token_id && position.asset && normalizeId(quote.token_id) === normalizeId(position.asset)) {
    return true;
  }
  const quoteName = quote.name.toLowerCase();
  const positionOutcome = (position.outcome ?? "").toLowerCase();
  return Boolean(quoteName && positionOutcome && quoteName === positionOutcome);
}

function accountStateStatusLabel(state: string) {
  if (state === "running") return "User WS 已连接";
  if (state === "connecting") return "User WS 连接中";
  if (state === "reconnecting") return "User WS 重连中";
  if (state === "config_missing") return "User WS 未配置";
  if (state === "stopped") return "User WS 已停止";
  return "REST 快照";
}

function accountStateErrorSummary(error: string) {
  if (error.includes("partially failed")) {
    const failedParts = [
      error.includes("balance:") ? "余额" : null,
      error.includes("orders:") ? "挂单" : null,
      error.includes("positions:") ? "仓位" : null,
    ].filter(Boolean);
    return failedParts.length > 0
      ? `${failedParts.join("、")}接口暂时同步失败，已保留可用快照。`
      : "账户状态部分同步失败，已保留可用快照。";
  }
  return "账户状态同步失败。";
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
