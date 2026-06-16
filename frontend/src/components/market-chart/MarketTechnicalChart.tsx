import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Logical,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp
} from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { CandleInterval } from "../../api/client";
import type { ChartComparisonLine, MarketCandle, MarketIndicatorPoint } from "./types";
import {
  candleTime,
  formatAxisTime,
  formatFixed,
  formatPrice,
  formatSigned,
  formatTooltipTime,
  indicatorTime,
  initialLookbackMs,
  intervalMs,
  nearestTimeValue,
  type TimeValue
} from "./utils";

export type MarketTechnicalChartProps = {
  symbol: string;
  interval: CandleInterval;
  candles: MarketCandle[];
  indicators: MarketIndicatorPoint[];
  showBollinger: boolean;
  showRsi: boolean;
  onLoadMore?: (startMs: number, endMs: number) => Promise<void>;
  isLoadingMore?: boolean;
  statusText?: string;
  indicatorStatusText?: string;
  loadingText?: string;
  fitAnchorVersion?: number;
  initialVisibleCandles?: number;
  comparisonLine?: ChartComparisonLine | null;
  countdownTargetMs?: number | null;
  toolbar?: ReactNode;
};

type BollKey = "middle" | "upper" | "lower";
type RsiKey = "rsi" | "rsi_ema";
type DiffKey = "rsi_ema_diff";
type RsiSeriesKey = RsiKey | "ref70" | "ref30";
type DiffSeriesKey = DiffKey | "ref0" | "ref12" | "ref-12";
type ChartThemeMode = "light" | "dark";

type TechnicalChartTheme = {
  background: string;
  text: string;
  grid: string;
  border: string;
  candleUp: string;
  candleDown: string;
  rsiReference: string;
  diffReference: string;
  diffBand: string;
};

const BOLL_LINES: Array<{ key: BollKey; color: string; label: string }> = [
  { key: "middle", color: "#64748b", label: "BOLL Mid" },
  { key: "upper", color: "#9333ea", label: "BOLL Upper" },
  { key: "lower", color: "#ea580c", label: "BOLL Lower" }
];

const RSI_LINES: Array<{ key: RsiKey; color: string; label: string }> = [
  { key: "rsi", color: "#ec4899", label: "RSI14" },
  { key: "rsi_ema", color: "#0ea5e9", label: "EMA14" }
];

const DIFF_LINES: Array<{ key: DiffKey; color: string; label: string }> = [
  { key: "rsi_ema_diff", color: "#f97316", label: "RSI-EMA" }
];

const INITIAL_VISIBLE_CANDLES = 50;
const MIN_VISIBLE_CANDLES = 30;
const ANCHOR_RIGHT_RATIO = 0.2;
const LOAD_MORE_THRESHOLD = 24;
const TARGET_BAR_WIDTH = 8;
const RSI_DEFAULT_RANGE = { from: 20, to: 80 };
const RSI_MAX_RANGE = { minValue: 0, maxValue: 100 };
const DIFF_DEFAULT_RANGE = { from: -15, to: 15 };

const CHART_THEMES: Record<ChartThemeMode, TechnicalChartTheme> = {
  dark: {
    background: "#080b10",
    text: "#94a3b8",
    grid: "#111827",
    border: "#1f2937",
    candleUp: "#0f7a4f",
    candleDown: "#b42318",
    rsiReference: "#94a3b8",
    diffReference: "#64748b",
    diffBand: "#cbd5e1"
  },
  light: {
    background: "#f8fafc",
    text: "#475569",
    grid: "#e2e8f0",
    border: "#cbd5e1",
    candleUp: "#047857",
    candleDown: "#dc2626",
    rsiReference: "#94a3b8",
    diffReference: "#64748b",
    diffBand: "#94a3b8"
  }
};

export default function MarketTechnicalChart({
  symbol,
  interval,
  candles,
  indicators,
  showBollinger,
  showRsi,
  onLoadMore,
  isLoadingMore = false,
  statusText,
  indicatorStatusText,
  loadingText = "加载历史中...",
  fitAnchorVersion = 0,
  initialVisibleCandles = INITIAL_VISIBLE_CANDLES,
  comparisonLine = null,
  countdownTargetMs = null,
  toolbar
}: MarketTechnicalChartProps) {
  const [themeMode, setThemeMode] = useState<ChartThemeMode>(() => readChartThemeMode());
  const chartTheme = CHART_THEMES[themeMode];
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const rsiContainerRef = useRef<HTMLDivElement | null>(null);
  const diffContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRootRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const countdownRef = useRef<HTMLDivElement | null>(null);

  const mainChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const diffChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const comparisonPriceLineRef = useRef<IPriceLine | null>(null);
  const bollSeriesRef = useRef<Partial<Record<BollKey, ISeriesApi<"Line">>>>({});
  const rsiSeriesRef = useRef<Partial<Record<RsiSeriesKey, ISeriesApi<"Line">>>>({});
  const diffSeriesRef = useRef<Partial<Record<DiffSeriesKey, ISeriesApi<"Line">>>>({});
  const rsiPrimarySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const diffPrimarySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  const candlesRef = useRef<MarketCandle[]>([]);
  const indicatorsRef = useRef<MarketIndicatorPoint[]>([]);
  const candleByTimeRef = useRef<Map<number, MarketCandle>>(new Map());
  const bollByTimeRef = useRef<Map<number, MarketIndicatorPoint["bollinger"]>>(new Map());
  const indicatorByTimeRef = useRef<Map<number, MarketIndicatorPoint>>(new Map());
  // 三个图表共用主 K 线时间轴，crosshair 数据需要投影到同一组 candle 时间点。
  const mainCrosshairDataRef = useRef<TimeValue[]>([]);
  const rsiCrosshairDataRef = useRef<TimeValue[]>([]);
  const diffCrosshairDataRef = useRef<TimeValue[]>([]);

  const initializedRef = useRef(false);
  const syncingRangeRef = useRef(false);
  // lightweight-charts 会分别触发每个图的 range 事件，这里用一帧合并避免互相递归同步。
  const pendingRangeRef = useRef<{ targets: IChartApi[]; range: LogicalRange } | null>(null);
  const rangeSyncFrameRef = useRef<number>(0);
  const syncingCrosshairRef = useRef(false);
  const programmaticCrosshairRef = useRef(false);
  const programmaticCrosshairFrameRef = useRef<number>(0);
  const lastCrosshairTimeRef = useRef<number | null>(null);
  const draggingRef = useRef(false);
  const loadMoreQueuedRef = useRef(false);
  const previousFirstTimeRef = useRef<number | null>(null);
  const previousLengthRef = useRef(0);
  const latestRangeRef = useRef<LogicalRange | null>(null);
  const resizeFrameRef = useRef<number>(0);
  const lastMainWidthRef = useRef(0);
  const rsiScaleInitializedRef = useRef(false);
  const diffScaleInitializedRef = useRef(false);

  const intervalRef = useRef(interval);
  const countdownTargetMsRef = useRef<number | null>(countdownTargetMs);
  const chartThemeRef = useRef<TechnicalChartTheme>(chartTheme);
  const showRsiRef = useRef(showRsi);
  const isLoadingMoreRef = useRef(isLoadingMore);
  const onLoadMoreRef = useRef(onLoadMore);

  const latestCandle = candles.at(-1);
  const latestIndicator = indicators.at(-1);
  const computedIndicatorStatus =
    latestIndicator?.rsi !== null && latestIndicator?.rsi !== undefined
      ? `RSI14 ${formatFixed(latestIndicator.rsi)} · EMA14 ${formatFixed(latestIndicator.rsi_ema)} · RSI-EMA ${formatSigned(latestIndicator.rsi_ema_diff)}`
      : showRsi
        ? "RSI 样本不足"
        : "";
  const headerStatus = [isLoadingMore ? loadingText : statusText, indicatorStatusText ?? computedIndicatorStatus]
    .filter(Boolean)
    .join(" · ");

  const chartOptions = useCallback(
    (
      container: HTMLDivElement,
      showTimeScale: boolean,
      priceScaleMargins = { top: 0.12, bottom: 0.12 },
      showTimeLabels = showTimeScale,
      minHeight = 120
    ) => ({
      autoSize: true,
      width: Math.max(320, container.clientWidth),
      height: Math.max(minHeight, container.clientHeight),
      localization: {
        locale: "zh-CN",
        timeFormatter: (time: Time) => {
          if (typeof time === "number") return formatTooltipTime(time);
          if (typeof time === "object" && "year" in time) {
            return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
          }
          return "";
        }
      },
      layout: {
        background: { type: ColorType.Solid, color: chartThemeRef.current.background },
        textColor: chartThemeRef.current.text,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
      },
      grid: {
        vertLines: { color: chartThemeRef.current.grid },
        horzLines: { color: chartThemeRef.current.grid }
      },
      rightPriceScale: {
        borderColor: chartThemeRef.current.border,
        minimumWidth: 86,
        scaleMargins: priceScaleMargins
      },
      timeScale: {
        visible: showTimeScale,
        borderColor: chartThemeRef.current.border,
        timeVisible: true,
        secondsVisible: false,
        allowShiftVisibleRangeOnWhitespace: true,
        tickMarkFormatter: (time: Time) => (showTimeLabels && typeof time === "number" ? formatAxisTime(time) : "")
      },
      crosshair: { mode: CrosshairMode.Normal }
    }),
    []
  );

  useEffect(() => {
    if (!chartRootRef.current || !mainContainerRef.current || !rsiContainerRef.current || !diffContainerRef.current) return;

    // 主图、RSI、diff 分成三个 chart，方便不同价格轴范围独立控制，但时间轴必须联动。
    const activeTheme = chartThemeRef.current;
    const mainChart = createChart(mainContainerRef.current, chartOptions(mainContainerRef.current, !showRsiRef.current));
    const rsiChart = createChart(rsiContainerRef.current, chartOptions(rsiContainerRef.current, false, { top: 0.06, bottom: 0.08 }, false, 1));
    const diffChart = createChart(diffContainerRef.current, chartOptions(diffContainerRef.current, true, { top: 0.12, bottom: 0.12 }, true, 1));
    const candleSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: activeTheme.candleUp,
      downColor: activeTheme.candleDown,
      borderUpColor: activeTheme.candleUp,
      borderDownColor: activeTheme.candleDown,
      wickUpColor: activeTheme.candleUp,
      wickDownColor: activeTheme.candleDown,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 }
    });

    mainChartRef.current = mainChart;
    rsiChartRef.current = rsiChart;
    diffChartRef.current = diffChart;
    candleSeriesRef.current = candleSeries;

    mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      const normalizedRange = normalizeVisibleRange(range);
      if (normalizedRange && range !== normalizedRange) {
        setVisibleRange(normalizedRange);
        return;
      }
      latestRangeRef.current = normalizedRange;
      syncTimeRange([rsiChart, diffChart], normalizedRange);
      maybeLoadMore(normalizedRange);
    });
    rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      const normalizedRange = normalizeVisibleRange(range);
      if (normalizedRange && range !== normalizedRange) {
        setVisibleRange(normalizedRange);
        return;
      }
      latestRangeRef.current = normalizedRange;
      syncTimeRange([mainChart, diffChart], normalizedRange);
    });
    diffChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      const normalizedRange = normalizeVisibleRange(range);
      if (normalizedRange && range !== normalizedRange) {
        setVisibleRange(normalizedRange);
        return;
      }
      latestRangeRef.current = normalizedRange;
      syncTimeRange([mainChart, rsiChart], normalizedRange);
    });

    mainChart.subscribeCrosshairMove((param) => {
      updateTooltipFromParam(param);
      syncCrosshair(param.time, rsiChart, rsiPrimarySeriesRef.current, rsiCrosshairDataRef.current);
      syncCrosshair(param.time, diffChart, diffPrimarySeriesRef.current, diffCrosshairDataRef.current);
    });
    rsiChart.subscribeCrosshairMove((param) => {
      updateTooltipFromParam(param);
      syncCrosshair(param.time, mainChart, candleSeries, mainCrosshairDataRef.current);
      syncCrosshair(param.time, diffChart, diffPrimarySeriesRef.current, diffCrosshairDataRef.current);
    });
    diffChart.subscribeCrosshairMove((param) => {
      updateTooltipFromParam(param);
      syncCrosshair(param.time, mainChart, candleSeries, mainCrosshairDataRef.current);
      syncCrosshair(param.time, rsiChart, rsiPrimarySeriesRef.current, rsiCrosshairDataRef.current);
    });

    const unbindMainDom = bindChartDom(mainContainerRef.current);
    const unbindRsiDom = bindChartDom(rsiContainerRef.current);
    const unbindDiffDom = bindChartDom(diffContainerRef.current);
    const resizeObserver = new ResizeObserver((entries) => {
      const mainRect = mainContainerRef.current?.getBoundingClientRect();
      if (!mainRect) return;
      const width = Math.round(mainRect.width);
      handleMainWidthChange(width);
      resizeCharts();
    });
    resizeObserver.observe(chartRootRef.current);

    return () => {
      unbindMainDom();
      unbindRsiDom();
      unbindDiffDom();
      if (rangeSyncFrameRef.current) window.cancelAnimationFrame(rangeSyncFrameRef.current);
      if (resizeFrameRef.current) window.cancelAnimationFrame(resizeFrameRef.current);
      if (programmaticCrosshairFrameRef.current) window.cancelAnimationFrame(programmaticCrosshairFrameRef.current);
      resizeObserver.disconnect();
      mainChart.remove();
      rsiChart.remove();
      diffChart.remove();
      mainChartRef.current = null;
      rsiChartRef.current = null;
      diffChartRef.current = null;
      candleSeriesRef.current = null;
      comparisonPriceLineRef.current = null;
      bollSeriesRef.current = {};
      rsiSeriesRef.current = {};
      diffSeriesRef.current = {};
      rsiPrimarySeriesRef.current = null;
      diffPrimarySeriesRef.current = null;
    };
  }, [chartOptions]);

  useEffect(() => {
    chartThemeRef.current = chartTheme;
    applyChartTheme(chartTheme);
  }, [chartTheme]);

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeMode(readChartThemeMode()));
    observer.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    intervalRef.current = interval;
    countdownTargetMsRef.current = countdownTargetMs;
    showRsiRef.current = showRsi;
    isLoadingMoreRef.current = isLoadingMore;
    onLoadMoreRef.current = onLoadMore;
  }, [interval, countdownTargetMs, showRsi, isLoadingMore, onLoadMore]);

  useEffect(() => {
    pendingRangeRef.current = null;
    if (rangeSyncFrameRef.current) {
      window.cancelAnimationFrame(rangeSyncFrameRef.current);
      rangeSyncFrameRef.current = 0;
    }
    if (resizeFrameRef.current) {
      window.cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = 0;
    }
    initializedRef.current = false;
    loadMoreQueuedRef.current = false;
    previousFirstTimeRef.current = null;
    previousLengthRef.current = 0;
    latestRangeRef.current = null;
    lastMainWidthRef.current = 0;
    lastCrosshairTimeRef.current = null;
    rsiScaleInitializedRef.current = false;
    diffScaleInitializedRef.current = false;
    mainChartRef.current?.clearCrosshairPosition();
    rsiChartRef.current?.clearCrosshairPosition();
    diffChartRef.current?.clearCrosshairPosition();
    hideTooltip();
  }, [symbol, interval]);

  useEffect(() => {
    indicatorsRef.current = indicators;
    rebuildMaps();
  }, [indicators]);

  useEffect(() => {
    const mainChart = mainChartRef.current;
    const rsiChart = rsiChartRef.current;
    const diffChart = diffChartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!mainChart || !rsiChart || !diffChart || !candleSeries) return;
    if (candles.some((candle) => candle.interval !== interval)) return;
    if (indicators.some((point) => point.interval !== interval)) return;

    const chartCandles = uniqueCandlesByChartTime(candles);
    const previousRange = mainChart.timeScale().getVisibleLogicalRange();
    const previousLength = candlesRef.current.length;
    const previousFirst = previousFirstTimeRef.current;
    const nextFirst = chartCandles[0] ? candleTime(chartCandles[0]) : null;
    const previousLast = previousLength > 0 ? candleTime(candlesRef.current[previousLength - 1]) : null;
    const nextLast = chartCandles.at(-1) ? candleTime(chartCandles.at(-1) as MarketCandle) : null;
    const addedBefore =
      previousFirst !== null && nextFirst !== null && nextFirst < previousFirst
        ? chartCandles.filter((candle) => candleTime(candle) < previousFirst).length
        : 0;
    const appendedAfter =
      previousLast !== null && nextLast !== null && nextLast > previousLast
        ? chartCandles.filter((candle) => candleTime(candle) > previousLast).length
        : 0;
    const wasAtRight = isNearRightEdge(previousRange, previousLengthRef.current);

    // 后端可能同时返回历史和实时数据，前端按图表时间去重后再重建所有派生索引。
    candlesRef.current = chartCandles;
    candleByTimeRef.current = new Map(chartCandles.map((candle) => [candleTime(candle), candle]));
    mainCrosshairDataRef.current = chartCandles.map((candle) => ({ time: candleTime(candle), value: candle.close }));
    rebuildMaps();

    candleSeries.setData(
      chartCandles.map((candle) => ({
        time: candleTime(candle) as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close
      }))
    );
    renderBollinger();
    renderComparisonLine();
    renderRsi();
    renderDiff();

    if (!initializedRef.current && chartCandles.length > 0) {
      initializedRef.current = true;
      setInitialVisibleRange();
    } else if (shouldReanchorAfterBootstrap(previousLength, chartCandles.length, addedBefore)) {
      setInitialVisibleRange();
    } else if (addedBefore > 0 && previousRange) {
      // 向左加载历史时保持用户当前视野不跳动，只按新增数量平移逻辑区间。
      setVisibleRange({
        from: (previousRange.from + addedBefore) as Logical,
        to: (previousRange.to + addedBefore) as Logical
      });
    } else if (appendedAfter > 0 && wasAtRight && previousRange && !draggingRef.current) {
      const width = previousRange.to - previousRange.from;
      setVisibleRange({
        from: (chartCandles.length + anchorRightPaddingBars(width) - width) as Logical,
        to: (chartCandles.length + anchorRightPaddingBars(width)) as Logical
      });
    } else if (previousRange) {
      setVisibleRange(previousRange);
    }

    previousFirstTimeRef.current = nextFirst;
    previousLengthRef.current = chartCandles.length;
    restoreCrosshairPosition();
  }, [candles, indicators, showBollinger, showRsi, interval, initialVisibleCandles]);

  useEffect(() => {
    const mainChart = mainChartRef.current;
    const rsiChart = rsiChartRef.current;
    const diffChart = diffChartRef.current;
    if (!mainChart || !rsiChart || !diffChart) return;
    mainChart.applyOptions({ timeScale: { visible: !showRsi } });
    rsiChart.applyOptions({ timeScale: { visible: false } });
    diffChart.applyOptions({ timeScale: { visible: true } });
    if (!showRsi) {
      rsiScaleInitializedRef.current = false;
      diffScaleInitializedRef.current = false;
    }
    window.requestAnimationFrame(() => {
      resizeCharts();
      renderRsi();
      renderDiff();
      if (latestRangeRef.current) setVisibleRange(latestRangeRef.current);
      restoreCrosshairPosition();
    });
  }, [showRsi]);

  useEffect(() => {
    const timer = window.setInterval(updateCountdown, 1000);
    updateCountdown();
    return () => window.clearInterval(timer);
  }, [countdownTargetMs, latestCandle]);

  useEffect(() => {
    if (fitAnchorVersion > 0 && candlesRef.current.length > 0) {
      window.requestAnimationFrame(() => setInitialVisibleRange());
    }
  }, [fitAnchorVersion, initialVisibleCandles]);

  useEffect(() => {
    renderComparisonLine();
    return () => removeComparisonLine();
  }, [comparisonLine]);

  function syncTimeRange(target: IChartApi | IChartApi[], range: LogicalRange | null) {
    if (!range || syncingRangeRef.current) return;
    pendingRangeRef.current = { targets: Array.isArray(target) ? target : [target], range };
    if (rangeSyncFrameRef.current) return;
    rangeSyncFrameRef.current = window.requestAnimationFrame(() => {
      rangeSyncFrameRef.current = 0;
      const pending = pendingRangeRef.current;
      pendingRangeRef.current = null;
      if (!pending) return;
      syncingRangeRef.current = true;
      try {
        for (const target of pending.targets) {
          target.timeScale().setVisibleLogicalRange(pending.range);
        }
      } finally {
        syncingRangeRef.current = false;
      }
    });
  }

  function applyChartTheme(theme: TechnicalChartTheme) {
    const chartVisualOptions = {
      layout: {
        background: { type: ColorType.Solid, color: theme.background },
        textColor: theme.text,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
      },
      grid: {
        vertLines: { color: theme.grid },
        horzLines: { color: theme.grid }
      },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border }
    };
    mainChartRef.current?.applyOptions(chartVisualOptions);
    rsiChartRef.current?.applyOptions(chartVisualOptions);
    diffChartRef.current?.applyOptions(chartVisualOptions);
    candleSeriesRef.current?.applyOptions({
      upColor: theme.candleUp,
      downColor: theme.candleDown,
      borderUpColor: theme.candleUp,
      borderDownColor: theme.candleDown,
      wickUpColor: theme.candleUp,
      wickDownColor: theme.candleDown
    });
    if (rsiChartRef.current) renderRsiReferenceLines(rsiChartRef.current, showRsiRef.current);
    if (diffChartRef.current) renderDiffReferenceLines(diffChartRef.current, showRsiRef.current);
    restoreCrosshairPosition();
  }

  function bindChartDom(element: HTMLDivElement) {
    const onPointerDown = () => {
      draggingRef.current = true;
      lastCrosshairTimeRef.current = null;
      hideTooltip();
    };
    const onPointerUp = () => {
      draggingRef.current = false;
    };
    const onMouseMove = (event: MouseEvent) => {
      if (event.buttons) return;
      const chart =
        element === mainContainerRef.current
          ? mainChartRef.current
          : element === rsiContainerRef.current
            ? rsiChartRef.current
            : diffChartRef.current;
      const scale = chart?.timeScale();
      if (!scale) return;
      const rect = element.getBoundingClientRect();
      const rawTime = scale.coordinateToTime(event.clientX - rect.left);
      const time = Number(rawTime);
      if (!Number.isFinite(time)) {
        hideTooltip();
        return;
      }
      const nearest = nearestTimeValue(mainCrosshairDataRef.current, time);
      const hoverTime = nearest?.time ?? time;
      lastCrosshairTimeRef.current = hoverTime;
      updateTooltipAt(hoverTime);
    };
    const onMouseLeave = () => {
      lastCrosshairTimeRef.current = null;
      hideTooltip();
    };
    const onDoubleClick = () => {
      resetIndicatorPriceScale(element);
    };

    element.addEventListener("pointerdown", onPointerDown, { passive: true });
    element.addEventListener("mousemove", onMouseMove, { passive: true });
    element.addEventListener("mouseleave", onMouseLeave);
    element.addEventListener("dblclick", onDoubleClick);
    window.addEventListener("pointerup", onPointerUp, { passive: true });
    window.addEventListener("pointercancel", onPointerUp, { passive: true });
    window.addEventListener("blur", onPointerUp, { passive: true });

    return () => {
      element.removeEventListener("pointerdown", onPointerDown);
      element.removeEventListener("mousemove", onMouseMove);
      element.removeEventListener("mouseleave", onMouseLeave);
      element.removeEventListener("dblclick", onDoubleClick);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      window.removeEventListener("blur", onPointerUp);
    };
  }

  function resetIndicatorPriceScale(element: HTMLDivElement) {
    if (element === rsiContainerRef.current) {
      rsiChartRef.current?.priceScale("right").setVisibleRange(RSI_DEFAULT_RANGE);
      return;
    }
    if (element === diffContainerRef.current) {
      const lineData = projectedIndicatorLineData("rsi_ema_diff");
      diffChartRef.current?.priceScale("right").setVisibleRange(
        lineData.length ? calculateDiffVisibleRange(lineData) : DIFF_DEFAULT_RANGE
      );
    }
  }

  function updateTooltipFromParam(param: MouseEventParams<Time>) {
    if (draggingRef.current || programmaticCrosshairRef.current || param.time === undefined) return;
    const nearest = nearestTimeValue(mainCrosshairDataRef.current, Number(param.time));
    if (!nearest) {
      hideTooltip();
      return;
    }
    lastCrosshairTimeRef.current = nearest.time;
    updateTooltipAt(nearest.time);
  }

  function syncCrosshair(
    time: Time | undefined,
    targetChart: IChartApi,
    targetSeries: ISeriesApi<"Line"> | ISeriesApi<"Candlestick"> | null,
    targetData: TimeValue[]
  ) {
    if (syncingCrosshairRef.current || !targetSeries || time === undefined) return;
    const sourceTime = Number(time);
    if (!Number.isFinite(sourceTime)) return;
    syncingCrosshairRef.current = true;
    try {
      setChartCrosshair(targetChart, targetSeries, targetData, sourceTime);
    } finally {
      syncingCrosshairRef.current = false;
    }
  }

  function setChartCrosshair(
    targetChart: IChartApi,
    targetSeries: ISeriesApi<"Line"> | ISeriesApi<"Candlestick"> | null,
    targetData: TimeValue[],
    time: number
  ) {
    if (!targetSeries) return;
    const nearest = nearestTimeValue(targetData, time);
    if (!nearest || !Number.isFinite(nearest.value)) {
      targetChart.clearCrosshairPosition();
      return;
    }
    markProgrammaticCrosshair();
    targetChart.setCrosshairPosition(nearest.value, time as UTCTimestamp, targetSeries);
  }

  function markProgrammaticCrosshair() {
    programmaticCrosshairRef.current = true;
    if (programmaticCrosshairFrameRef.current) window.cancelAnimationFrame(programmaticCrosshairFrameRef.current);
    programmaticCrosshairFrameRef.current = window.requestAnimationFrame(() => {
      programmaticCrosshairFrameRef.current = 0;
      programmaticCrosshairRef.current = false;
    });
  }

  function restoreCrosshairPosition() {
    const time = lastCrosshairTimeRef.current;
    if (time === null || !Number.isFinite(time)) return;
    const mainChart = mainChartRef.current;
    const rsiChart = rsiChartRef.current;
    const diffChart = diffChartRef.current;
    if (!mainChart) return;
    // 数据刷新会触发 lightweight-charts 内部 crosshair 事件；刷新后按用户最后 hover 的 K 线恢复位置。
    setChartCrosshair(mainChart, candleSeriesRef.current, mainCrosshairDataRef.current, time);
    if (showRsiRef.current && rsiChart && diffChart) {
      setChartCrosshair(rsiChart, rsiPrimarySeriesRef.current, rsiCrosshairDataRef.current, time);
      setChartCrosshair(diffChart, diffPrimarySeriesRef.current, diffCrosshairDataRef.current, time);
    }
    updateTooltipAt(time);
  }

  function maybeLoadMore(range: LogicalRange | null) {
    const loadMore = onLoadMoreRef.current;
    if (!range || !loadMore || isLoadingMoreRef.current || loadMoreQueuedRef.current || !candlesRef.current.length) {
      return;
    }
    if (range.from > LOAD_MORE_THRESHOLD) return;
    const first = candlesRef.current[0];
    const currentInterval = intervalRef.current;
    const endMs = new Date(first.open_time).getTime() - intervalMs(currentInterval);
    const startMs = Math.max(0, endMs - initialLookbackMs(currentInterval));
    loadMoreQueuedRef.current = true;
    // 拖动过程中 range 变化很密集，稍微延迟并串行化历史加载，避免重复打 API。
    window.setTimeout(() => {
      void loadMore(startMs, endMs).finally(() => {
        loadMoreQueuedRef.current = false;
      });
    }, 120);
  }

  function rebuildMaps() {
    const activeIndicators = indicatorsRef.current;
    const bollByTime = new Map<number, MarketIndicatorPoint["bollinger"]>();
    const indicatorByTime = new Map<number, MarketIndicatorPoint>();
    for (const point of activeIndicators) {
      // 指标时间可能来自 warmup 后的序列，展示时贴到最近 candle，保证 tooltip 和曲线同轴。
      const nearest = nearestTimeValue(mainCrosshairDataRef.current, indicatorTime(point));
      if (!nearest) continue;
      if (point.bollinger.middle !== null) bollByTime.set(nearest.time, point.bollinger);
      if (point.rsi !== null) indicatorByTime.set(nearest.time, point);
    }
    bollByTimeRef.current = bollByTime;
    indicatorByTimeRef.current = indicatorByTime;
  }

  function renderBollinger() {
    const chart = mainChartRef.current;
    if (!chart) return;
    for (const line of BOLL_LINES) {
      let series = bollSeriesRef.current[line.key];
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: showBollinger,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 }
        });
        bollSeriesRef.current[line.key] = series;
      }
      series.applyOptions({ color: line.color, visible: showBollinger });
      const valuesByTime = new Map<number, number>();
      for (const point of indicators) {
        const value = point.bollinger[line.key];
        if (value === null || !Number.isFinite(value)) continue;
        const nearest = nearestTimeValue(mainCrosshairDataRef.current, indicatorTime(point));
        if (nearest) valuesByTime.set(nearest.time, value);
      }
      if (valuesByTime.size === 0) {
        series.setData([]);
        continue;
      }
      series.setData(
        candlesRef.current.map((candle) => {
          const time = candleTime(candle) as UTCTimestamp;
          const value = valuesByTime.get(time);
          return Number.isFinite(value) ? { time, value } : { time };
        })
      );
    }
  }

  function renderComparisonLine() {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;
    removeComparisonLine();
    if (!comparisonLine || !Number.isFinite(comparisonLine.price)) return;
    // 比较线来自 Polymarket 当前窗口的起始 K 线 open，属于业务基准价而不是交易概率。
    comparisonPriceLineRef.current = candleSeries.createPriceLine({
      price: comparisonLine.price,
      color: comparisonLine.color,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: comparisonLine.title,
    });
  }

  function removeComparisonLine() {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries || !comparisonPriceLineRef.current) return;
    candleSeries.removePriceLine(comparisonPriceLineRef.current);
    comparisonPriceLineRef.current = null;
  }

  function renderRsi() {
    const chart = rsiChartRef.current;
    if (!chart) return;
    for (const line of RSI_LINES) {
      let series = rsiSeriesRef.current[line.key];
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: showRsi,
          priceScaleId: "right",
          priceFormat: { type: "custom", formatter: (value: number) => value.toFixed(2) },
          autoscaleInfoProvider: () => ({ priceRange: RSI_MAX_RANGE })
        });
        rsiSeriesRef.current[line.key] = series;
        if (line.key === "rsi") rsiPrimarySeriesRef.current = series;
      }
      series.applyOptions({ color: line.color, visible: showRsi });
      const lineData = projectedIndicatorLineData(line.key);
      if (lineData.length === 0) {
        series.setData([]);
        if (line.key === "rsi") rsiCrosshairDataRef.current = [];
        continue;
      }
      series.setData(fullTimelineLineData(lineData));
      if (line.key === "rsi") rsiCrosshairDataRef.current = fullTimelineCrosshairData(lineData);
    }
    renderRsiReferenceLines(chart, showRsi);
    chart.priceScale("right").applyOptions({
      autoScale: false,
      mode: 0,
      scaleMargins: { top: 0.06, bottom: 0.08 }
    });
    if (showRsi && !rsiScaleInitializedRef.current) {
      chart.priceScale("right").setVisibleRange(RSI_DEFAULT_RANGE);
      rsiScaleInitializedRef.current = true;
    }
  }

  function renderDiff() {
    const chart = diffChartRef.current;
    if (!chart) return;
    for (const line of DIFF_LINES) {
      let series = diffSeriesRef.current[line.key];
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: showRsi,
          priceFormat: { type: "custom", formatter: (value: number) => value.toFixed(2) }
        });
        diffSeriesRef.current[line.key] = series;
        diffPrimarySeriesRef.current = series;
      }
      series.applyOptions({ color: line.color, visible: showRsi });
      const lineData = projectedIndicatorLineData(line.key);
      if (lineData.length === 0) {
        series.setData([]);
        diffCrosshairDataRef.current = [];
        continue;
      }
      series.setData(fullTimelineLineData(lineData));
      diffCrosshairDataRef.current = fullTimelineCrosshairData(lineData);
    }
    renderDiffReferenceLines(chart, showRsi);
    chart.priceScale("right").applyOptions({
      autoScale: false,
      mode: 0,
      scaleMargins: { top: 0.12, bottom: 0.12 }
    });
    if (showRsi && !diffScaleInitializedRef.current) {
      // 首次打开时按当前数据给 diff 一个合适范围；之后实时刷新不再覆盖用户手动缩放。
      const lineData = projectedIndicatorLineData("rsi_ema_diff");
      chart.priceScale("right").setVisibleRange(lineData.length ? calculateDiffVisibleRange(lineData) : DIFF_DEFAULT_RANGE);
      diffScaleInitializedRef.current = true;
    }
  }

  function calculateDiffVisibleRange(lineData: TimeValue[]) {
    let minValue = DIFF_DEFAULT_RANGE.from;
    let maxValue = DIFF_DEFAULT_RANGE.to;
    for (const point of lineData) {
      if (!Number.isFinite(point.value)) continue;
      minValue = Math.min(minValue, point.value);
      maxValue = Math.max(maxValue, point.value);
    }
    const span = Math.max(maxValue - minValue, 1);
    const padding = Math.max(span * 0.15, 2);
    return {
      from: minValue - padding,
      to: maxValue + padding
    };
  }

  function renderRsiReferenceLines(chart: IChartApi, shouldRender: boolean) {
    const first = candlesRef.current[0];
    const last = candlesRef.current.at(-1);
    const theme = chartThemeRef.current;
    const refs = [
      { key: "ref70", value: 70, color: theme.rsiReference, lineStyle: LineStyle.Dashed },
      { key: "ref30", value: 30, color: theme.rsiReference, lineStyle: LineStyle.Dashed }
    ] as const;
    for (const refLine of refs) {
      let series = rsiSeriesRef.current[refLine.key];
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: refLine.color,
          lineStyle: refLine.lineStyle,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: shouldRender
        });
        rsiSeriesRef.current[refLine.key] = series;
      }
      series.applyOptions({ color: refLine.color, visible: shouldRender });
      if (!first || !last) {
        series.setData([]);
      } else if (candleTime(first) === candleTime(last)) {
        series.setData([{ time: candleTime(first) as UTCTimestamp, value: refLine.value }]);
      } else {
        series.setData([
          { time: candleTime(first) as UTCTimestamp, value: refLine.value },
          { time: candleTime(last) as UTCTimestamp, value: refLine.value }
        ]);
      }
    }
  }

  function renderDiffReferenceLines(chart: IChartApi, shouldRender: boolean) {
    const first = candlesRef.current[0];
    const last = candlesRef.current.at(-1);
    const theme = chartThemeRef.current;
    const refs = [
      { key: "ref0", value: 0, color: theme.diffReference, lineStyle: LineStyle.Dashed },
      { key: "ref12", value: 12, color: theme.diffBand, lineStyle: LineStyle.Solid },
      { key: "ref-12", value: -12, color: theme.diffBand, lineStyle: LineStyle.Solid }
    ] as const;
    for (const refLine of refs) {
      let series = diffSeriesRef.current[refLine.key];
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: refLine.color,
          lineStyle: refLine.lineStyle,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: shouldRender
        });
        diffSeriesRef.current[refLine.key] = series;
      }
      series.applyOptions({ color: refLine.color, visible: shouldRender });
      if (!first || !last) {
        series.setData([]);
      } else if (candleTime(first) === candleTime(last)) {
        series.setData([{ time: candleTime(first) as UTCTimestamp, value: refLine.value }]);
      } else {
        series.setData([
          { time: candleTime(first) as UTCTimestamp, value: refLine.value },
          { time: candleTime(last) as UTCTimestamp, value: refLine.value }
        ]);
      }
    }
  }

  function fullTimelineCrosshairData(validData: TimeValue[]) {
    const result: TimeValue[] = [];
    if (!validData.length) return result;
    let cursor = 0;
    let latestValue = validData[0].value;
    for (const candle of candlesRef.current) {
      const time = candleTime(candle);
      while (cursor < validData.length && validData[cursor].time <= time) {
        latestValue = validData[cursor].value;
        cursor += 1;
      }
      // crosshair 需要每根 K 线都有值；曲线本身仍只在真实指标点上画线。
      result.push({ time, value: latestValue });
    }
    return result;
  }

  function fullTimelineLineData(validData: TimeValue[]) {
    const valuesByTime = new Map(validData.map((point) => [point.time, point.value]));
    return candlesRef.current.map((candle) => {
      const time = candleTime(candle) as UTCTimestamp;
      const value = valuesByTime.get(time);
      return Number.isFinite(value) ? { time, value } : { time };
    });
  }

  function projectedIndicatorLineData(key: RsiKey | DiffKey) {
    const valuesByTime = new Map<number, number>();
    for (const point of indicators) {
      const value = point[key];
      if (value === null || !Number.isFinite(value)) continue;
      const nearest = nearestTimeValue(mainCrosshairDataRef.current, indicatorTime(point));
      if (nearest) valuesByTime.set(nearest.time, value);
    }
    return Array.from(valuesByTime.entries())
      .sort(([left], [right]) => left - right)
      .map(([time, value]) => ({ time, value }));
  }

  function setInitialVisibleRange() {
    const visibleBars = Math.min(candlesRef.current.length, initialVisibleCandles);
    const rightPadding = anchorRightPaddingBars(visibleBars);
    setVisibleRange({
      from: (candlesRef.current.length - visibleBars - 0.5) as Logical,
      to: (candlesRef.current.length + rightPadding) as Logical
    });
  }

  function shouldReanchorAfterBootstrap(previousLength: number, nextLength: number, addedBefore: number) {
    if (previousLength <= 0) return false;
    if (previousLength >= initialVisibleCandles) return false;
    if (addedBefore > 0) return nextLength >= Math.min(initialVisibleCandles, previousLength + addedBefore);
    return nextLength >= initialVisibleCandles;
  }

  function handleMainWidthChange(nextWidth: number) {
    const previousWidth = lastMainWidthRef.current;
    lastMainWidthRef.current = nextWidth;
    if (previousWidth <= 0 || nextWidth <= 0 || Math.abs(nextWidth - previousWidth) < 24) return;
    if (resizeFrameRef.current) window.cancelAnimationFrame(resizeFrameRef.current);
    resizeFrameRef.current = window.requestAnimationFrame(() => {
      resizeFrameRef.current = 0;
      const range = mainChartRef.current?.timeScale().getVisibleLogicalRange();
      if (!range || candlesRef.current.length <= 0) return;
      const currentBars = range.to - range.from;
      if (!Number.isFinite(currentBars) || currentBars <= 0) return;
      const nextBars = Math.min(candlesRef.current.length, Math.max(minimumVisibleBars(), currentBars * (nextWidth / previousWidth)));
      const wasAtRight = isNearRightEdge(range, candlesRef.current.length);
      if (wasAtRight) {
        const rightPadding = anchorRightPaddingBars(nextBars);
        setVisibleRange({
          from: (candlesRef.current.length + rightPadding - nextBars) as Logical,
          to: (candlesRef.current.length + rightPadding) as Logical
        });
        return;
      }
      const center = (range.from + range.to) / 2;
      setVisibleRange({
        from: (center - nextBars / 2) as Logical,
        to: (center + nextBars / 2) as Logical
      });
    });
  }

  function anchorRightPaddingBars(visibleBars: number) {
    return Math.max(1, Math.round(visibleBars * (ANCHOR_RIGHT_RATIO / (1 - ANCHOR_RIGHT_RATIO))));
  }

  function normalizeVisibleRange(range: LogicalRange | null) {
    if (!range || syncingRangeRef.current || candlesRef.current.length <= 0) return range;
    const minVisibleBars = minimumVisibleBars();
    const currentBars = range.to - range.from;
    if (!Number.isFinite(currentBars) || currentBars >= minVisibleBars) return range;

    const center = (range.from + range.to) / 2;
    const half = minVisibleBars / 2;
    return {
      from: (center - half) as Logical,
      to: (center + half) as Logical
    };
  }

  function minimumVisibleBars() {
    return Math.min(candlesRef.current.length, MIN_VISIBLE_CANDLES);
  }

  function setVisibleRange(range: LogicalRange) {
    latestRangeRef.current = range;
    mainChartRef.current?.timeScale().setVisibleLogicalRange(range);
    rsiChartRef.current?.timeScale().setVisibleLogicalRange(range);
    diffChartRef.current?.timeScale().setVisibleLogicalRange(range);
  }

  function resizeCharts() {
    const main = mainContainerRef.current;
    const rsi = rsiContainerRef.current;
    const diff = diffContainerRef.current;
    if (main && mainChartRef.current) {
      mainChartRef.current.resize(Math.max(320, main.clientWidth), Math.max(120, main.clientHeight), true);
    }
    if (rsi && rsiChartRef.current && showRsiRef.current) {
      rsiChartRef.current.resize(Math.max(320, rsi.clientWidth), Math.max(1, rsi.clientHeight), true);
    }
    if (diff && diffChartRef.current && showRsiRef.current) {
      diffChartRef.current.resize(Math.max(320, diff.clientWidth), Math.max(1, diff.clientHeight), true);
    }
  }

  function updateCountdown() {
    const element = countdownRef.current;
    const targetMs = countdownTargetMsRef.current;
    if (!element || targetMs == null || !Number.isFinite(targetMs)) {
      if (element) element.hidden = true;
      return;
    }
    const remainingSeconds = Math.max(0, Math.ceil((targetMs - Date.now()) / 1000));
    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = remainingSeconds % 60;
    element.textContent = `${minutes}:${String(seconds).padStart(2, "0")}`;
    element.hidden = false;
  }

  function updateTooltipAt(time: number) {
    const tooltip = tooltipRef.current;
    if (!tooltip) return;
    const indicator = indicatorByTimeRef.current.get(time);
    if (!indicator) {
      hideTooltip();
      return;
    }

    const rows = [`<strong>${formatTooltipTime(time)}</strong> <span class="muted">${escapeHtml(symbol)} ${interval}</span>`];
    rows.push(
      `RSI14 ${formatFixed(indicator.rsi)} · EMA14 ${formatFixed(indicator.rsi_ema)} · RSI-EMA ${formatDiffHtml(indicator.rsi_ema_diff)}`
    );
    tooltip.innerHTML = rows.join("<br />");
    tooltip.hidden = false;
  }

  function hideTooltip() {
    if (tooltipRef.current) tooltipRef.current.hidden = true;
  }

  return (
    <div ref={chartRootRef} className={showRsi ? "btc-watch-chart" : "btc-watch-chart btc-watch-chart-single"}>
      {toolbar && <div className="btc-chart-toolbar">{toolbar}</div>}
      <section className="btc-chart-panel btc-main-panel">
        <div className="btc-chart-title">
          <div className="btc-chart-legend">
            {showBollinger &&
              BOLL_LINES.map((line) => (
                <span className="legend-item" key={line.key}>
                  <span className="dot" style={{ "--color": line.color } as React.CSSProperties} />
                  {line.label}
                </span>
              ))}
          </div>
          <div className="btc-chart-status">
            {headerStatus}
          </div>
        </div>
        <div className="btc-chart-canvas btc-main-chart" ref={mainContainerRef}>
          <div className="kline-countdown" ref={countdownRef} hidden />
          <div className="chart-tooltip" ref={tooltipRef} hidden />
        </div>
      </section>
      <section className="btc-chart-panel btc-rsi-panel" hidden={!showRsi}>
        <div className="btc-chart-canvas btc-rsi-chart" ref={rsiContainerRef} />
      </section>
      <section className="btc-chart-panel btc-diff-panel" hidden={!showRsi}>
        <div className="btc-chart-canvas btc-diff-chart" ref={diffContainerRef} />
      </section>
    </div>
  );
}

function isNearRightEdge(range: LogicalRange | null, length: number) {
  if (!range || length <= 0) return true;
  const viewportStillContainsLatestBars = range.from < length - 1;
  const rightEdgeNearLatestBars = range.to >= length - 4;
  return viewportStillContainsLatestBars && rightEdgeNearLatestBars;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    if (char === '"') return "&quot;";
    return "&#039;";
  });
}

function formatDiffHtml(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '<span class="diff-normal">n/a</span>';
  }
  const abs = Math.abs(value);
  const className = abs > 15 ? "diff-strong" : abs > 12 ? "diff-watch" : "diff-normal";
  return `<span class="${className}">${value.toFixed(2)}</span>`;
}

function readChartThemeMode(): ChartThemeMode {
  if (typeof document === "undefined") return "light";
  return document.body.dataset.theme === "dark" ? "dark" : "light";
}

function uniqueCandlesByChartTime(candles: MarketCandle[]) {
  const byTime = new Map<string, MarketCandle>();
  for (const candle of candles) {
    byTime.set(`${candle.symbol}:${candle.interval}:${candleTime(candle)}`, candle);
  }
  return Array.from(byTime.values()).sort((left, right) => candleTime(left) - candleTime(right));
}
