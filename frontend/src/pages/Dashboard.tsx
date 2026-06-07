import { useEffect, useState } from "react";
import { api, candleIntervals, type BotStatus, type Candle, type CandleInterval, type Indicators, type Orderbook, type PolyMarket, type Signal } from "../api/client";
import CandleChart from "../components/CandleChart";
import OrderbookPanel from "../components/OrderbookPanel";
import StatusBar from "../components/StatusBar";

export default function Dashboard() {
  const [interval, setInterval] = useState<CandleInterval>("1m");
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [markets, setMarkets] = useState<PolyMarket[]>([]);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [indicators, setIndicators] = useState<Indicators | null>(null);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [orderbook, setOrderbook] = useState<Orderbook | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    const load = () => {
      void Promise.all([
        api.status().then(setStatus),
        api.markets().then(setMarkets),
        api.candles(interval, 120).then(setCandles),
        api.indicators().then(setIndicators),
        api.latestSignal().then(setSignal),
        api.orderbook().then(setOrderbook)
      ]).then(() => setLastRefresh(new Date()));
    };

    load();
    const timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, [interval]);

  return (
    <div className="page-stack">
      <StatusBar status={status} />
      <section className="summary-grid">
        <div className="kpi">
          <span>Dry-run</span>
          <strong>{status?.config.dry_run ? "On" : "Off"}</strong>
        </div>
        <div className="kpi">
          <span>Trading</span>
          <strong>{status?.config.trading_enabled ? "Enabled" : "Disabled"}</strong>
        </div>
        <div className="kpi">
          <span>Active markets</span>
          <strong>{markets.filter((market) => market.status === "active").length}</strong>
        </div>
        <div className="kpi">
          <span>API refresh</span>
          <strong>{lastRefresh ? lastRefresh.toLocaleTimeString() : "-"}</strong>
        </div>
      </section>
      <section className="content-grid">
        <div className="panel wide">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">BTCUSDT live candles</p>
              <h2>{interval} closed candle stream</h2>
            </div>
            <span className="metric">{candles.length ? candles[candles.length - 1].close.toLocaleString() : "-"}</span>
          </div>
          <div className="segmented-control">
            {candleIntervals.map((item) => (
              <button className={item === interval ? "active" : ""} key={item} onClick={() => setInterval(item)}>
                {item}
              </button>
            ))}
          </div>
          <CandleChart candles={candles} />
        </div>
        <OrderbookPanel orderbook={orderbook} markets={markets} />
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Indicators</p>
            <h2>Latest RSI and Bollinger</h2>
          </div>
        </div>
        <div className="indicator-grid">
          {indicators &&
            Object.entries(indicators.intervals).map(([interval, item]) => (
              <div className="indicator-card" key={interval}>
                <span>{interval}</span>
                <strong>RSI {item.rsi == null ? "-" : item.rsi.toFixed(1)}</strong>
                <p>
                  {item.trend} · BB {formatBand(item.bollinger.lower)} / {formatBand(item.bollinger.middle)} / {formatBand(item.bollinger.upper)}
                </p>
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}

function formatBand(value: number | null) {
  return value == null ? "-" : value.toFixed(0);
}
