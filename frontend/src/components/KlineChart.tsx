import {
  CandlestickSeries,
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candle } from "../api/client";

type Props = {
  candles: Candle[];
};

export default function KlineChart({ candles }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1017" },
        textColor: "#aab6c5"
      },
      grid: {
        vertLines: { color: "#151d29" },
        horzLines: { color: "#151d29" }
      },
      rightPriceScale: {
        borderColor: "#263244"
      },
      timeScale: {
        borderColor: "#263244",
        timeVisible: true,
        secondsVisible: false
      }
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#16a34a",
      downColor: "#dc2626",
      borderVisible: false,
      wickUpColor: "#16a34a",
      wickDownColor: "#dc2626"
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    series.setData(
      candles.map((candle) => ({
        time: Math.floor(new Date(candle.open_time).getTime() / 1000) as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close
      }))
    );
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  return <div className="kline-chart" ref={containerRef} />;
}

