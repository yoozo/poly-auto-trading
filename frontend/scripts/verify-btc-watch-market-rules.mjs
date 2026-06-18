import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const scenarioSource = `
  import assert from "node:assert/strict";
  import {
    baselineStartMsForMarket,
    candleOpenAnchorMs,
    candleAtOpenTime,
    marketChartFocusKey,
    marketComparisonTarget,
    marketFocusAnchorMs,
    polymarketDisplayWindow,
    selectedPolymarketMarket,
  } from "../src/pages/btcWatchMarketRules";

  const baseMarket = {
    id: "btc-updown-4h-1781798400",
    condition_id: null,
    slug: "bitcoin-up-or-down-june-18-12pm-et",
    title: "Bitcoin Up or Down - June 18, 12PM-4PM ET",
    series_slug: null,
    interval: "4h",
    start_time: "2026-06-18T16:05:00.000Z",
    end_time: "2026-06-18T20:00:00.000Z",
    window: "current",
    seconds_to_start: null,
    seconds_to_end: 3600,
    accepting_orders: true,
    volume: 100,
    liquidity: 100,
    updated_at: "2026-06-18T16:10:00.000Z",
    outcome_quotes: [],
  };
  const nextMarket = {
    ...baseMarket,
    id: "btc-updown-4h-next",
    title: "Bitcoin Up or Down - June 18, 4PM-8PM ET",
    start_time: "2026-06-18T20:05:00.000Z",
    end_time: "2026-06-19T00:00:00.000Z",
    window: "next",
  };
  const current5mMarket = {
    ...baseMarket,
    id: "btc-updown-5m-current",
    title: "Bitcoin Up or Down - June 18, 12:05PM ET",
    interval: "5m",
    start_time: "2026-06-18T16:05:00.000Z",
    end_time: "2026-06-18T16:10:00.000Z",
  };

  const window4h = polymarketDisplayWindow(baseMarket);
  assert.ok(window4h, "4h market window should parse from title");
  assert.equal(new Date(window4h.startMs).toISOString(), "2026-06-18T16:00:00.000Z");
  assert.equal(new Date(window4h.endMs).toISOString(), "2026-06-18T20:00:00.000Z");

  const targetStartMs = baselineStartMsForMarket(window4h);
  assert.equal(targetStartMs, window4h.startMs, "target line baseline is market window start at 1m precision");
  const targetLine = marketComparisonTarget(baseMarket, Date.parse("2026-06-18T16:10:00.000Z"));
  assert.equal(targetLine?.key, baseMarket.id + ":" + targetStartMs);
  assert.equal(targetLine?.baselineStartMs, targetStartMs);
  assert.equal(targetLine?.marketInterval, "4h");
  assert.equal(
    marketComparisonTarget({ ...baseMarket, updated_at: "2026-06-18T16:11:00.000Z" }, Date.parse("2026-06-18T16:11:00.000Z"))?.key,
    targetLine?.key,
    "quote refreshes must not change the target line request key"
  );
  assert.equal(
    marketComparisonTarget(nextMarket, Date.parse("2026-06-18T19:59:00.000Z")),
    null,
    "future market target line should wait until its start candle can exist"
  );
  assert.equal(
    marketComparisonTarget(nextMarket, Date.parse("2026-06-18T20:00:00.000Z"))?.baselineStartMs,
    Date.parse("2026-06-18T20:00:00.000Z"),
    "target line appears as soon as the selected future market reaches its start"
  );
  const baselineRows = [
    candleAt("2026-06-18T15:59:00.000Z", 9990),
    candleAt("2026-06-18T16:00:00.000Z", 10000),
    candleAt("2026-06-18T16:01:00.000Z", 10010),
  ];
  assert.equal(candleAtOpenTime(baselineRows, targetStartMs)?.open, 10000);
  assert.equal(
    candleAtOpenTime([candleAt("2026-06-18T16:01:00.000Z", 10010)], targetStartMs),
    null,
    "target line must not use a neighboring 1m candle when the exact market-start open is missing"
  );

  for (const interval of ["1m", "5m", "15m", "1h", "4h"]) {
    assert.equal(
      baselineStartMsForMarket(window4h),
      targetStartMs,
      "target baseline must not vary when active K-line interval is " + interval
    );
  }

  assert.equal(candleOpenAnchorMs(window4h.startMs, "1m"), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.equal(candleOpenAnchorMs(window4h.startMs, "5m"), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.equal(candleOpenAnchorMs(window4h.startMs, "15m"), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.equal(candleOpenAnchorMs(window4h.startMs, "1h"), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.equal(candleOpenAnchorMs(window4h.startMs, "4h"), Date.parse("2026-06-18T16:00:00.000Z"));
  const currentTimeIn4hMarket = Date.parse("2026-06-18T19:37:42.000Z");
  assert.equal(marketFocusAnchorMs(window4h, "1m", currentTimeIn4hMarket), Date.parse("2026-06-18T19:37:00.000Z"));
  assert.equal(marketFocusAnchorMs(window4h, "5m", currentTimeIn4hMarket), Date.parse("2026-06-18T19:35:00.000Z"));
  assert.equal(marketFocusAnchorMs(window4h, "4h", currentTimeIn4hMarket), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.equal(
    marketFocusAnchorMs(window4h, "1m", Date.parse("2026-06-18T15:59:00.000Z")),
    Date.parse("2026-06-18T16:00:00.000Z"),
    "future market focus clamps to market start"
  );
  assert.equal(
    marketFocusAnchorMs(window4h, "1m", Date.parse("2026-06-18T20:01:00.000Z")),
    Date.parse("2026-06-18T19:59:00.000Z"),
    "expired market focus clamps to last candle inside the market window"
  );
  assert.notEqual(
    marketChartFocusKey({
      nonce: 1,
      marketId: baseMarket.id,
      focusAnchorMs: marketFocusAnchorMs(window4h, "4h", currentTimeIn4hMarket),
      candleInterval: "4h",
    }),
    marketChartFocusKey({
      nonce: 1,
      marketId: baseMarket.id,
      focusAnchorMs: marketFocusAnchorMs(window4h, "1m", currentTimeIn4hMarket),
      candleInterval: "1m",
    }),
    "same market must re-anchor current time when switching from 4h K-line to 1m K-line"
  );
  const offGrid4hMarket = {
    ...baseMarket,
    id: "btc-updown-4h-off-grid",
    title: "Bitcoin Up or Down - June 18, 12:02PM-4:02PM ET",
    start_time: "2026-06-18T16:03:00.000Z",
    end_time: "2026-06-18T20:02:00.000Z",
  };
  const offGridWindow = polymarketDisplayWindow(offGrid4hMarket);
  assert.ok(offGridWindow, "off-grid 4h market window should parse from title");
  assert.equal(marketFocusAnchorMs(offGridWindow, "1m", currentTimeIn4hMarket), Date.parse("2026-06-18T19:37:00.000Z"));
  assert.equal(marketFocusAnchorMs(offGridWindow, "4h", currentTimeIn4hMarket), Date.parse("2026-06-18T16:00:00.000Z"));
  assert.notEqual(
    marketChartFocusKey({
      nonce: 1,
      marketId: offGrid4hMarket.id,
      focusAnchorMs: marketFocusAnchorMs(offGridWindow, "4h", currentTimeIn4hMarket),
      candleInterval: "4h",
    }),
    marketChartFocusKey({
      nonce: 1,
      marketId: offGrid4hMarket.id,
      focusAnchorMs: marketFocusAnchorMs(offGridWindow, "1m", currentTimeIn4hMarket),
      candleInterval: "1m",
    }),
    "off-grid 4h market must use a distinct current-time 1m focus instead of reusing the 4h anchor"
  );

  const selectedFromSnapshot = selectedPolymarketMarket({
    markets: [current5mMarket, nextMarket],
    selectedMarketId: baseMarket.id,
    selectedMarketSnapshot: baseMarket,
  });
  assert.equal(selectedFromSnapshot?.id, baseMarket.id, "fixed market must not fall back to current if snapshot temporarily omits it");
  assert.equal(selectedFromSnapshot?.interval, "4h", "fixed 4h market must stay 4h when active K-line changes");

  const selectedDefault = selectedPolymarketMarket({
    markets: [current5mMarket, nextMarket],
    selectedMarketId: null,
    selectedMarketSnapshot: null,
  });
  assert.equal(selectedDefault?.id, current5mMarket.id, "without a fixed selection the current market is used");

  const selectedFresh = selectedPolymarketMarket({
    markets: [{ ...baseMarket, liquidity: 200 }, current5mMarket],
    selectedMarketId: baseMarket.id,
    selectedMarketSnapshot: baseMarket,
  });
  assert.equal(selectedFresh?.liquidity, 200, "fresh snapshot data should replace the pinned copy when id is present");

  const marketMatrix = [
    {
      market: {
        ...baseMarket,
        id: "btc-updown-5m-1781798700",
        title: "Bitcoin Up or Down - June 18, 12:05PM ET",
        interval: "5m",
        start_time: "2026-06-18T16:06:00.000Z",
        end_time: "2026-06-18T16:10:00.000Z",
      },
      startIso: "2026-06-18T16:05:00.000Z",
    },
    {
      market: {
        ...baseMarket,
        id: "btc-updown-15m-1781798400",
        title: "Bitcoin Up or Down - June 18, 12:00PM ET",
        interval: "15m",
        start_time: "2026-06-18T16:01:00.000Z",
        end_time: "2026-06-18T16:15:00.000Z",
      },
      startIso: "2026-06-18T16:00:00.000Z",
    },
    {
      market: {
        ...baseMarket,
        id: "btc-updown-1h-1781798400",
        title: "Bitcoin Up or Down - June 18, 12PM ET",
        interval: "1h",
        start_time: "2026-06-18T16:02:00.000Z",
        end_time: "2026-06-18T17:00:00.000Z",
      },
      startIso: "2026-06-18T16:00:00.000Z",
    },
    { market: baseMarket, startIso: "2026-06-18T16:00:00.000Z" },
  ];
  for (const { market, startIso } of marketMatrix) {
    const marketWindow = polymarketDisplayWindow(market);
    assert.ok(marketWindow, "market window should parse for " + market.interval);
    assert.equal(new Date(marketWindow.startMs).toISOString(), startIso, "window start should use Polymarket title for " + market.interval);
    const targetBaseline = baselineStartMsForMarket(marketWindow);
    const comparisonTarget = marketComparisonTarget(market, marketWindow.startMs);
    assert.equal(comparisonTarget?.baselineStartMs, targetBaseline, "comparison target should use 1m baseline for " + market.interval);
    for (const activeInterval of ["1m", "5m", "15m", "1h", "4h"]) {
      assert.equal(
        selectedPolymarketMarket({
          markets: [{ ...market, updated_at: "2026-06-18T16:11:00.000Z" }, current5mMarket],
          selectedMarketId: market.id,
          selectedMarketSnapshot: market,
        })?.id,
        market.id,
        "active K-line " + activeInterval + " must not switch fixed market " + market.interval
      );
      assert.equal(
        baselineStartMsForMarket(marketWindow),
        targetBaseline,
        "target line baseline must stay tied to market start for " + market.interval + " market on " + activeInterval + " K-line"
      );
      assert.equal(
        candleOpenAnchorMs(marketWindow.startMs, activeInterval),
        expectedOpen(marketWindow.startMs, activeInterval),
        "focus anchor should be the containing K-line open for " + market.interval + " market on " + activeInterval + " K-line"
      );
    }
  }

  console.log("BTC watch market rule scenarios passed");

  function candleAt(openTime, open) {
    return {
      symbol: "BTCUSDT",
      interval: "1m",
      open_time: openTime,
      close_time: new Date(Date.parse(openTime) + 60_000).toISOString(),
      open,
      high: open + 1,
      low: open - 1,
      close: open,
      volume: 1,
      is_closed: true,
    };
  }

  function expectedOpen(timeMs, interval) {
    const stepMs = {
      "1m": 60_000,
      "5m": 5 * 60_000,
      "15m": 15 * 60_000,
      "1h": 60 * 60_000,
      "4h": 4 * 60 * 60_000,
    }[interval];
    return Math.floor(timeMs / stepMs) * stepMs;
  }
`;

const tempDir = await mkdtemp(join(tmpdir(), "btc-watch-rules-"));
const outfile = join(tempDir, "scenarios.mjs");

try {
  await build({
    stdin: {
      contents: scenarioSource,
      sourcefile: "btc-watch-market-rules.scenarios.ts",
      resolveDir: join(process.cwd(), "scripts"),
      loader: "ts",
    },
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    logLevel: "silent",
  });
  await import(pathToFileURL(outfile).href);
} finally {
  await rm(tempDir, { recursive: true, force: true });
}
