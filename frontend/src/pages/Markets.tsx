import { useCallback, useState } from "react";
import { api, type PolyMarket } from "../api/client";
import MarketTable from "../components/MarketTable";
import { usePolling } from "../hooks/usePolling";

type Props = {
  onOpenMarket: (market: PolyMarket) => void;
};

export default function Markets({ onOpenMarket }: Props) {
  const [markets, setMarkets] = useState<PolyMarket[]>([]);
  const { lastRefresh, error } = usePolling(
    useCallback(async () => {
      setMarkets(await api.markets());
    }, [])
  );
  const visibleMarkets = markets.filter((market) => !isExpired(market));

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Polymarket</p>
            <h2>BTC 5m / 15m markets</h2>
          </div>
          <span className="metric">{visibleMarkets.length} live · {lastRefresh ? lastRefresh.toLocaleTimeString() : "loading"}</span>
        </div>
        {error && <p className="error-text">{error}</p>}
        {visibleMarkets.length ? (
          <MarketTable markets={visibleMarkets} onOpenMarket={onOpenMarket} />
        ) : (
          <div className="empty-state">
            <strong>No BTC 5m/15m markets tracked</strong>
            <p>Polymarket discovery is running, but the current BTC short-window slug candidates have not returned active markets yet.</p>
          </div>
        )}
      </section>
      <section className="token-grid">
        {visibleMarkets.map((market) => (
          <article className="panel compact" key={`${market.id}-tokens`}>
            <p className="eyebrow">{market.interval}</p>
            <button className="card-title-button" onClick={() => onOpenMarket(market)}>{market.title}</button>
            <dl className="details">
              <div>
                <dt>YES</dt>
                <dd>{market.yes_token_id}</dd>
              </div>
              <div>
                <dt>NO</dt>
                <dd>{market.no_token_id}</dd>
              </div>
            </dl>
          </article>
        ))}
      </section>
    </div>
  );
}

function isExpired(market: PolyMarket) {
  return Boolean(market.end_time && new Date(market.end_time).getTime() <= Date.now());
}
