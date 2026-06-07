import { ArrowLeft, ExternalLink, Radio } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type MarketResult, type Orderbook, type PolyMarket } from "../api/client";
import { usePolymarketMarketWs } from "../hooks/usePolymarketMarketWs";
import { formatCents, formatCompact } from "../utils/format";

type Props = {
  market: PolyMarket;
  onBack: () => void;
};

export default function MarketDetail({ market, onBack }: Props) {
  const [now, setNow] = useState(() => Date.now());
  const [result, setResult] = useState<MarketResult | null>(null);
  const [resultError, setResultError] = useState<string | null>(null);
  const expired = isExpired(market, now);
  const { yesBook, noBook, ticks, wsState, lastMessageAt, error } = usePolymarketMarketWs(market.yes_token_id, market.no_token_id, !expired);
  const yesMid = useMemo(() => midPrice(yesBook), [yesBook]);
  const noMid = useMemo(() => midPrice(noBook), [noBook]);
  const polymarketUrl = market.event_slug ? `https://polymarket.com/event/${market.event_slug}` : null;

  useEffect(() => {
    if (!market.end_time) return;
    const endTime = new Date(market.end_time).getTime();
    const delay = Math.max(endTime - Date.now(), 0);
    const timeout = window.setTimeout(() => setNow(Date.now()), delay);
    return () => window.clearTimeout(timeout);
  }, [market.end_time]);

  useEffect(() => {
    setResult(null);
    setResultError(null);
  }, [market.id]);

  useEffect(() => {
    if (!expired || !market.event_slug) return;
    let active = true;
    void api.marketResult(market.event_slug)
      .then((nextResult) => {
        if (!active) return;
        setResult(nextResult);
        setResultError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setResultError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [expired, market.event_slug]);

  return (
    <div className="page-stack">
      <section className="panel market-detail-head">
        <div className="market-actions">
          <button className="icon-text-button" onClick={onBack}>
            <ArrowLeft size={16} />
            <span>Markets</span>
          </button>
          {polymarketUrl && (
            <a className="icon-text-button primary" href={polymarketUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
              <span>Open Polymarket</span>
            </a>
          )}
        </div>
        <div>
          <p className="eyebrow">Read-only market page</p>
          <h2>{market.title}</h2>
        </div>
        <span className="metric">{market.interval} · {lastMessageAt ? lastMessageAt.toLocaleTimeString() : wsState}</span>
      </section>

      {error && <p className="error-text">{error}</p>}
      {expired && (
        <MarketResultBanner result={result} resultError={resultError} />
      )}

      <section className="market-trade-grid">
        <TokenPricePanel side="UP / YES" book={yesBook} mid={yesMid} tokenId={market.yes_token_id} />
        <TokenPricePanel side="DOWN / NO" book={noBook} mid={noMid} tokenId={market.no_token_id} />
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Realtime changes</p>
              <h2>YES / NO price stream</h2>
            </div>
            <span className="metric"><Radio size={13} /> Polymarket WS · {wsState}</span>
          </div>
          <div className="price-tape">
            {ticks.length ? ticks.map((tick) => (
              <div className="price-tick" key={tick.at.toISOString()}>
                <time>{tick.at.toLocaleTimeString()}</time>
                <span>YES {formatPair(tick.yesBid, tick.yesAsk)}</span>
                <span>NO {formatPair(tick.noBid, tick.noAsk)}</span>
              </div>
            )) : <div className="empty-row">Waiting for first market WS update</div>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Market metadata</p>
              <h2>Contract</h2>
            </div>
            <span className={`badge ${market.status}`}>{market.status}</span>
          </div>
          <dl className="details">
            <div>
              <dt>Condition</dt>
              <dd>{market.condition_id}</dd>
            </div>
            <div>
              <dt>Ends</dt>
              <dd>{market.end_time ? new Date(market.end_time).toLocaleString() : "-"}</dd>
            </div>
            <div>
              <dt>Market ID</dt>
              <dd>{market.id}</dd>
            </div>
          </dl>
        </div>
      </section>
    </div>
  );
}

function MarketResultBanner({ result, resultError }: { result: MarketResult | null; resultError: string | null }) {
  const winner = normalizeWinner(result?.winning_outcome ?? null);
  return (
    <div className={`result-banner ${winner ? "resolved" : "pending"}`}>
      <div>
        <p className="eyebrow">Closed market result</p>
        <strong>{winner ? `${winner} won` : "Settlement pending"}</strong>
        <span>{resultSummary(result, resultError)}</span>
      </div>
    </div>
  );
}

function normalizeWinner(outcome: string | null) {
  if (!outcome) return null;
  const normalized = outcome.toLowerCase();
  if (normalized === "up" || normalized === "yes") return "UP / YES";
  if (normalized === "down" || normalized === "no") return "DOWN / NO";
  return outcome;
}

function resultSummary(result: MarketResult | null, resultError: string | null) {
  if (resultError) return resultError;
  if (!result) return "Fetching final Polymarket result...";
  if (result.result_status === "resolved" && result.outcomes.length) {
    return result.outcomes
      .map((outcome, index) => `${outcome}: ${formatCents(result.outcome_prices[index] ?? null)}`)
      .join(" · ");
  }
  return "Polymarket has closed the market, but the final result is not available yet.";
}

function isExpired(market: PolyMarket, now: number) {
  return Boolean(market.end_time && new Date(market.end_time).getTime() <= now);
}

function TokenPricePanel({ side, book, mid, tokenId }: { side: string; book: Orderbook | null; mid: number | null; tokenId: string }) {
  return (
    <section className="panel token-price-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{side}</p>
          <h2>{formatCents(mid)}</h2>
        </div>
        <span className="metric">Spread {formatCents(book?.spread ?? null)}</span>
      </div>
      <div className="quote-grid">
        <Quote label="Best bid" value={formatCents(book?.best_bid ?? null)} />
        <Quote label="Best ask" value={formatCents(book?.best_ask ?? null)} />
        <Quote label="Liquidity" value={formatCompact(book?.liquidity ?? null)} />
      </div>
      <dl className="details">
        <div>
          <dt>Token ID</dt>
          <dd>{tokenId}</dd>
        </div>
      </dl>
    </section>
  );
}

function Quote({ label, value }: { label: string; value: string }) {
  return (
    <div className="quote-box">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function midPrice(book: Orderbook | null) {
  if (!book?.best_bid || !book.best_ask) return null;
  return (book.best_bid + book.best_ask) / 2;
}

function formatPair(bid: number | null, ask: number | null) {
  return `${formatCents(bid)} / ${formatCents(ask)}`;
}
