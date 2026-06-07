import type { PolyMarket } from "../api/client";
import { formatCents } from "../utils/format";

type Props = {
  markets: PolyMarket[];
  onOpenMarket?: (market: PolyMarket) => void;
};

export default function MarketTable({ markets, onOpenMarket }: Props) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Market</th>
            <th>Interval</th>
            <th>Ends</th>
            <th>Bid</th>
            <th>Ask</th>
            <th>Spread</th>
            <th>Liquidity</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {markets.map((market) => (
            <tr key={market.id}>
              <td>
                <button className="market-link" onClick={() => onOpenMarket?.(market)}>
                  {market.title}
                </button>
                <span className="muted mono">{market.condition_id}</span>
              </td>
              <td>{market.interval}</td>
              <td>{market.end_time ? new Date(market.end_time).toLocaleTimeString() : "-"}</td>
              <td>{formatCents(market.best_bid)}</td>
              <td>{formatCents(market.best_ask)}</td>
              <td className={market.spread != null && market.spread > 0.04 ? "warn" : ""}>{formatCents(market.spread)}</td>
              <td>{market.liquidity == null ? "-" : market.liquidity.toLocaleString()}</td>
              <td>
                <span className={`badge ${market.status}`}>{market.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
