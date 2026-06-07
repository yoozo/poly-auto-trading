import type { Orderbook, PolyMarket } from "../api/client";
import { formatCents, formatCompact } from "../utils/format";

type Props = {
  orderbook: Orderbook | null;
  markets?: PolyMarket[];
};

export default function OrderbookPanel({ orderbook, markets = [] }: Props) {
  if (!orderbook) {
    return <div className="panel skeleton">Loading orderbook...</div>;
  }

  const context = getOrderbookContext(orderbook, markets);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div className="book-title-block">
          <p className="eyebrow">Orderbook</p>
          <h2>{context.title}</h2>
          <span>{context.subtitle}</span>
        </div>
        <span className="metric">Spread {formatCents(orderbook.spread)}</span>
      </div>
      <div className="book-summary">
        <div>
          <span>Best bid</span>
          <strong>{formatCents(orderbook.best_bid)}</strong>
        </div>
        <div>
          <span>Best ask</span>
          <strong>{formatCents(orderbook.best_ask)}</strong>
        </div>
        <div>
          <span>Liquidity</span>
          <strong>{formatCompact(orderbook.liquidity)}</strong>
        </div>
        <div>
          <span>Updated</span>
          <strong>{formatTime(orderbook.updated_at)}</strong>
        </div>
      </div>
      <div className="book-grid">
        <div>
          <h3>Bids</h3>
          {orderbook.bids.length ? (
            orderbook.bids.map((level) => (
              <div className="book-row bid" key={`bid-${level.price}`}>
                <span>{formatCents(level.price)}</span>
                <strong>{level.size.toLocaleString()}</strong>
              </div>
            ))
          ) : (
            <div className="empty-row">No bids yet</div>
          )}
        </div>
        <div>
          <h3>Asks</h3>
          {orderbook.asks.length ? (
            orderbook.asks.map((level) => (
              <div className="book-row ask" key={`ask-${level.price}`}>
                <span>{formatCents(level.price)}</span>
                <strong>{level.size.toLocaleString()}</strong>
              </div>
            ))
          ) : (
            <div className="empty-row">No asks yet</div>
          )}
        </div>
      </div>
    </section>
  );
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleTimeString() : "--";
}

function shortToken(tokenId: string) {
  return tokenId ? `${tokenId.slice(0, 8)}...${tokenId.slice(-6)}` : "waiting for WS";
}

function getOrderbookContext(orderbook: Orderbook, markets: PolyMarket[]) {
  const market = markets.find((item) => item.yes_token_id === orderbook.token_id || item.no_token_id === orderbook.token_id);
  if (!market) {
    return {
      title: orderbook.token_id ? "Tracked token" : "Waiting for market WS",
      subtitle: orderbook.token_id ? shortToken(orderbook.token_id) : "No orderbook snapshot received yet"
    };
  }

  const side = market.yes_token_id === orderbook.token_id ? "UP / YES" : "DOWN / NO";
  const ends = market.end_time ? `ends ${new Date(market.end_time).toLocaleTimeString()}` : "no end time";
  return {
    title: `BTC ${market.interval} · ${side}`,
    subtitle: `${market.title} · ${ends} · ${shortToken(orderbook.token_id)}`
  };
}
