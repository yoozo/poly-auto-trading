import { useEffect, useMemo, useState } from "react";
import type { Orderbook } from "../api/client";

const POLYMARKET_MARKET_WS_URL =
  import.meta.env.VITE_POLYMARKET_MARKET_WS_URL ?? "wss://ws-subscriptions-clob.polymarket.com/ws/market";
const RENDER_THROTTLE_MS = 500;

type WsState = "idle" | "connecting" | "connected" | "reconnecting" | "error" | "ended";

type PriceTick = {
  at: Date;
  yesBid: number | null;
  yesAsk: number | null;
  noBid: number | null;
  noAsk: number | null;
};

type UsePolymarketMarketWsResult = {
  yesBook: Orderbook | null;
  noBook: Orderbook | null;
  ticks: PriceTick[];
  wsState: WsState;
  lastMessageAt: Date | null;
  error: string | null;
};

export function usePolymarketMarketWs(yesTokenId: string, noTokenId: string, enabled = true): UsePolymarketMarketWsResult {
  const [books, setBooks] = useState<Record<string, Orderbook>>({});
  const [ticks, setTicks] = useState<PriceTick[]>([]);
  const [wsState, setWsState] = useState<WsState>("idle");
  const [lastMessageAt, setLastMessageAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tokenIds = useMemo(() => [yesTokenId, noTokenId].filter(Boolean), [noTokenId, yesTokenId]);

  useEffect(() => {
    setBooks({});
    setTicks([]);
    setLastMessageAt(null);
    setError(null);
  }, [enabled, noTokenId, yesTokenId]);

  useEffect(() => {
    if (!enabled) {
      setWsState("ended");
      return;
    }
    if (tokenIds.length === 0) return;

    let websocket: WebSocket | null = null;
    let stopped = false;
    let reconnectTimer = 0;
    let pingTimer = 0;
    let flushTimer = 0;
    let reconnectDelay = 1000;
    let pendingUpdates: Orderbook[] = [];
    let pendingAt: Date | null = null;

    const flushPendingUpdates = () => {
      if (stopped || !pendingUpdates.length || !pendingAt) return;
      const updates = pendingUpdates;
      const receivedAt = pendingAt;
      pendingUpdates = [];
      pendingAt = null;

      setLastMessageAt(receivedAt);
      setBooks((current) => {
        const next = { ...current };
        for (const update of updates) {
          if (!tokenIds.includes(update.token_id)) continue;
          next[update.token_id] = mergeOrderbook(next[update.token_id], update, receivedAt);
        }
        const yesBook = next[yesTokenId];
        const noBook = next[noTokenId];
        setTicks((existingTicks) => [
          {
            at: receivedAt,
            yesBid: yesBook?.best_bid ?? null,
            yesAsk: yesBook?.best_ask ?? null,
            noBid: noBook?.best_bid ?? null,
            noAsk: noBook?.best_ask ?? null
          },
          ...existingTicks
        ].slice(0, 24));
        return next;
      });
    };

    const connect = () => {
      setWsState(reconnectDelay === 1000 ? "connecting" : "reconnecting");
      websocket = new WebSocket(POLYMARKET_MARKET_WS_URL);

      websocket.addEventListener("open", () => {
        if (!websocket) return;
        reconnectDelay = 1000;
        setWsState("connected");
        setError(null);
        websocket.send(JSON.stringify({ type: "market", assets_ids: tokenIds, custom_feature_enabled: true }));
        pingTimer = window.setInterval(() => websocket?.send("PING"), 10000);
      });

      websocket.addEventListener("message", (event) => {
        if (stopped) return;
        if (event.data === "PONG") return;
        const updates = parseWsMessage(event.data);
        if (!updates.length) return;
        pendingUpdates.push(...updates);
        pendingAt = new Date();
      });

      websocket.addEventListener("error", () => {
        setWsState("error");
        setError("Polymarket market WS connection failed");
      });

      websocket.addEventListener("close", () => {
        window.clearInterval(pingTimer);
        if (stopped) return;
        setWsState("reconnecting");
        reconnectTimer = window.setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
      });
    };

    flushTimer = window.setInterval(flushPendingUpdates, RENDER_THROTTLE_MS);
    connect();

    return () => {
      stopped = true;
      pendingUpdates = [];
      pendingAt = null;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(pingTimer);
      window.clearInterval(flushTimer);
      websocket?.close();
    };
  }, [enabled, noTokenId, tokenIds, yesTokenId]);

  return {
    yesBook: books[yesTokenId] ?? null,
    noBook: books[noTokenId] ?? null,
    ticks,
    wsState,
    lastMessageAt,
    error
  };
}

function parseWsMessage(raw: string): Orderbook[] {
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return [];
  }

  const messages = Array.isArray(payload) ? payload : [payload];
  return messages.flatMap((message) => {
    if (!isRecord(message)) return [];
    const eventType = String(message.event_type ?? message.type ?? "");
    if (eventType === "book" || ("bids" in message && "asks" in message)) {
      const tokenId = tokenIdFrom(message);
      if (!tokenId) return [];
      return [buildOrderbook(tokenId, levels(message.bids), levels(message.asks), maybeNumber(message.best_bid ?? message.bestBid), maybeNumber(message.best_ask ?? message.bestAsk), true)];
    }

    if (eventType === "best_bid_ask") {
      const tokenId = tokenIdFrom(message);
      if (!tokenId) return [];
      return [buildOrderbook(tokenId, [], [], maybeNumber(message.best_bid ?? message.bestBid), maybeNumber(message.best_ask ?? message.bestAsk), false)];
    }

    const rawChanges = message.price_changes ?? message.changes;
    const changes = Array.isArray(rawChanges) ? rawChanges : [message];
    return changes.flatMap((change) => {
      if (!isRecord(change)) return [];
      const tokenId = tokenIdFrom(change) || tokenIdFrom(message);
      if (!tokenId) return [];
      const side = String(change.side ?? "").toLowerCase();
      const price = maybeNumber(change.price ?? change.p);
      const size = maybeNumber(change.size ?? change.s);
      const bids = side === "buy" || side === "bid" ? levelFrom(price, size) : [];
      const asks = side === "sell" || side === "ask" ? levelFrom(price, size) : [];
      return [buildOrderbook(tokenId, bids, asks, maybeNumber(change.best_bid ?? change.bestBid ?? message.best_bid ?? message.bestBid), maybeNumber(change.best_ask ?? change.bestAsk ?? message.best_ask ?? message.bestAsk), false)];
    });
  });
}

function mergeOrderbook(existing: Orderbook | undefined, update: Orderbook, at: Date): Orderbook {
  const bids = mergeLevels(existing?.bids ?? [], update.bids, "desc");
  const asks = mergeLevels(existing?.asks ?? [], update.asks, "asc");
  const bestBid = update.best_bid ?? bids[0]?.price ?? existing?.best_bid ?? null;
  const bestAsk = update.best_ask ?? asks[0]?.price ?? existing?.best_ask ?? null;
  return {
    token_id: update.token_id,
    best_bid: bestBid,
    best_ask: bestAsk,
    spread: bestBid !== null && bestAsk !== null ? Number((bestAsk - bestBid).toFixed(4)) : null,
    liquidity: [...bids, ...asks].reduce((total, level) => total + level.size, 0),
    updated_at: at.toISOString(),
    bids,
    asks
  };
}

function buildOrderbook(
  tokenId: string,
  bids: Orderbook["bids"],
  asks: Orderbook["asks"],
  bestBid: number | null,
  bestAsk: number | null,
  deriveBestFromLevels: boolean
): Orderbook {
  const sortedBids = [...bids].sort((a, b) => b.price - a.price);
  const sortedAsks = [...asks].sort((a, b) => a.price - b.price);
  return {
    token_id: tokenId,
    best_bid: bestBid ?? (deriveBestFromLevels ? sortedBids[0]?.price ?? null : null),
    best_ask: bestAsk ?? (deriveBestFromLevels ? sortedAsks[0]?.price ?? null : null),
    spread: null,
    liquidity: null,
    updated_at: null,
    bids: sortedBids,
    asks: sortedAsks
  };
}

function mergeLevels(current: Orderbook["bids"], updates: Orderbook["bids"], sort: "asc" | "desc") {
  const byPrice = new Map(current.map((level) => [level.price, level]));
  for (const update of updates) {
    if (update.size <= 0) byPrice.delete(update.price);
    else byPrice.set(update.price, update);
  }
  return [...byPrice.values()].sort((a, b) => sort === "asc" ? a.price - b.price : b.price - a.price).slice(0, 20);
}

function levels(raw: unknown): Orderbook["bids"] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const price = Array.isArray(item) ? maybeNumber(item[0]) : isRecord(item) ? maybeNumber(item.price ?? item.p) : null;
    const size = Array.isArray(item) ? maybeNumber(item[1]) : isRecord(item) ? maybeNumber(item.size ?? item.s) : null;
    return levelFrom(price, size);
  });
}

function levelFrom(price: number | null, size: number | null): Orderbook["bids"] {
  return price === null || size === null ? [] : [{ price, size }];
}

function tokenIdFrom(payload: Record<string, unknown>) {
  return String(payload.asset_id ?? payload.assetId ?? payload.token_id ?? payload.tokenId ?? "");
}

function maybeNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
