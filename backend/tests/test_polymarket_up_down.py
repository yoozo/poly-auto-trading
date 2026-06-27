import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpcore
import httpx
import pytest
from polymarket import TransportError

from app.api import routes_polymarket
from app.services.polymarket_client import (
    PolymarketClient,
    is_btc_up_down_event,
    is_retryable_polymarket_sdk_error,
    normalize_up_down_market,
    select_up_down_windows,
    sdk_activity_to_data_row,
    sdk_event_to_gamma_dict,
    sdk_order_book_to_clob_dict,
    sdk_profile_to_gamma_dict,
    sdk_series_to_gamma_events,
)
from app.services.polymarket_market_store import PolymarketUpDownStore


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def test_filters_btc_5m_series() -> None:
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-5m", "title": "x"}, interval="5m")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-15m", "title": "x"}, interval="15m")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-hourly", "title": "x"}, interval="1h")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-4h", "title": "x"}, interval="4h")
    assert not is_btc_up_down_event({"seriesSlug": "eth-up-or-down-5m", "title": "x"}, interval="5m")


def test_parse_btc_up_down_subscribe_message_accepts_valid_interval() -> None:
    assert routes_polymarket.parse_btc_up_down_subscribe_message(
        '{"type":"polymarket.btc_up_down.subscribe","interval":"15m"}'
    ) == "15m"


def test_parse_btc_up_down_subscribe_message_rejects_invalid_payload() -> None:
    assert routes_polymarket.parse_btc_up_down_subscribe_message("not-json") is None
    assert routes_polymarket.parse_btc_up_down_subscribe_message('{"type":"noop","interval":"15m"}') is None
    assert routes_polymarket.parse_btc_up_down_subscribe_message(
        '{"type":"polymarket.btc_up_down.subscribe","interval":"1m"}'
    ) is None


def test_parse_btc_up_down_market_subscribe_message_accepts_market_id() -> None:
    message = routes_polymarket.parse_btc_up_down_market_subscribe_message(
        '{"type":"polymarket.btc_up_down.market.subscribe","interval":"5m","market_id":"market-1"}'
    )

    assert message == routes_polymarket.BtcUpDownMarketSubscribeMessage(interval="5m", market_id="market-1")


def test_parse_btc_up_down_market_subscribe_message_accepts_request_id() -> None:
    message = routes_polymarket.parse_btc_up_down_market_subscribe_message(
        '{"type":"polymarket.btc_up_down.market.subscribe","interval":"5m","market_id":"market-1","request_id":"perf-1"}'
    )

    assert message == routes_polymarket.BtcUpDownMarketSubscribeMessage(
        interval="5m",
        market_id="market-1",
        request_id="perf-1",
    )


@pytest.mark.asyncio
async def test_send_btc_up_down_pong_echoes_request_id() -> None:
    websocket = RecordingWebSocket()

    handled = await routes_polymarket.send_btc_up_down_pong(
        websocket,
        '{"type":"polymarket.btc_up_down.ping","request_id":"latency-1"}',
    )

    assert handled is True
    assert websocket.sent == [{"type": "polymarket.btc_up_down.pong", "request_id": "latency-1"}]


@pytest.mark.asyncio
async def test_send_btc_up_down_pong_ignores_non_ping() -> None:
    websocket = RecordingWebSocket()

    handled = await routes_polymarket.send_btc_up_down_pong(
        websocket,
        '{"type":"polymarket.btc_up_down.subscribe","interval":"5m"}',
    )

    assert handled is False
    assert websocket.sent == []


def test_polymarket_sdk_retryable_error_detects_wrapped_httpx_connect_error() -> None:
    try:
        raise RuntimeError("sdk transport failed") from httpx.ConnectError("offline")
    except RuntimeError as exc:
        assert is_retryable_polymarket_sdk_error(exc)


def test_polymarket_sdk_retryable_error_detects_wrapped_httpcore_connect_error() -> None:
    try:
        raise RuntimeError("sdk transport failed") from httpcore.ConnectError()
    except RuntimeError as exc:
        assert is_retryable_polymarket_sdk_error(exc)


def test_selects_current_and_next_btc_windows() -> None:
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    events = [
        make_event("old", "2026-06-13T04:55:00Z", "2026-06-13T05:00:00Z"),
        make_event("current", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
        make_event("next", "2026-06-13T05:10:00Z", "2026-06-13T05:15:00Z"),
    ]

    selected = select_up_down_windows(events, now=now, limit=2)

    assert [event["slug"] for event in selected] == ["old", "current"]


def test_selects_recent_closed_current_and_next_btc_windows() -> None:
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    events = [
        make_event("older", "2026-06-13T04:55:00Z", "2026-06-13T05:00:00Z"),
        make_event("closed", "2026-06-13T05:00:00Z", "2026-06-13T05:05:00Z"),
        make_event("current", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
        make_event("next", "2026-06-13T05:10:00Z", "2026-06-13T05:15:00Z"),
        make_event("future", "2026-06-13T05:15:00Z", "2026-06-13T05:20:00Z"),
    ]

    selected = select_up_down_windows(events, now=now, limit=4)

    assert [event["slug"] for event in selected] == ["closed", "current", "next", "future"]


def test_normalize_up_down_market_prefers_event_start_time_over_market_creation_time() -> None:
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    event = make_event("future", "2026-06-13T05:10:00Z", "2026-06-13T05:15:00Z")
    event["startTime"] = "2026-06-13T05:10:00Z"
    event["markets"][0]["eventStartTime"] = "2026-06-12T23:00:00Z"

    market = normalize_up_down_market(event, interval="5m", books={}, now=now)

    assert market.window == "upcoming"
    assert market.start_time == datetime(2026, 6, 13, 5, 10, tzinfo=timezone.utc)


def test_normalize_up_down_market_infers_hourly_start_from_end_time() -> None:
    now = datetime(2026, 6, 14, 12, 30, tzinfo=timezone.utc)
    event = make_event("bitcoin-up-or-down-june-14-2026-8am-et", "2026-06-12T12:00:00Z", "2026-06-14T13:00:00Z")
    event["seriesSlug"] = "btc-up-or-down-hourly"
    event["startTime"] = None
    event["markets"][0]["eventStartTime"] = "2026-06-12T12:00:00Z"

    market = normalize_up_down_market(event, interval="1h", books={}, now=now)

    assert market.window == "current"
    assert market.start_time == datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)


def test_normalize_up_down_market_aligns_outcomes_with_order_books() -> None:
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    event = make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z")
    books = {
        "up-token": {
            "asset_id": "up-token",
            "bids": [{"price": "0.47", "size": "10"}],
            "asks": [{"price": "0.49", "size": "7"}],
            "last_trade_price": "0.48",
        },
        "down-token": {
            "asset_id": "down-token",
            "bids": [{"price": "0.51", "size": "9"}],
            "asks": [{"price": "0.53", "size": "8"}],
            "last_trade_price": "0.52",
        },
    }

    market = normalize_up_down_market(event, interval="5m", books=books, now=now)

    assert market.window == "current"
    assert [quote.name for quote in market.outcome_quotes] == ["Up", "Down"]
    assert market.outcome_quotes[0].token_id == "up-token"
    assert market.outcome_quotes[0].best_bid is not None
    assert str(market.outcome_quotes[0].best_bid) == "0.47"
    assert str(market.outcome_quotes[0].buy_price) == "0.49"


def test_sdk_event_adapter_keeps_existing_up_down_normalizer_shape() -> None:
    event = sdk_event_to_gamma_dict(
        {
            "id": "event-1",
            "slug": "btc-up-or-down-5m-1",
            "title": "Bitcoin Up or Down - June 13",
            "sports": {"series_slug": "btc-up-or-down-5m"},
            "schedule": {"start_time": "2026-06-13T05:05:00Z", "end_date": "2026-06-13T05:10:00Z"},
            "markets": [
                {
                    "id": "market-1",
                    "slug": "btc-up-or-down-5m-1",
                    "condition_id": "0xabc",
                    "question": "Bitcoin Up or Down - June 13",
                    "state": {
                        "start_date": "2026-06-13T05:05:00Z",
                        "end_date": "2026-06-13T05:10:00Z",
                        "accepting_orders": True,
                    },
                    "metrics": {"volume_num": "100", "liquidity_num": "200"},
                    "outcomes": {
                        "yes": {"label": "Up", "token_id": "up-token", "price": "0.48"},
                        "no": {"label": "Down", "token_id": "down-token", "price": "0.52"},
                    },
                }
            ],
        }
    )

    market = normalize_up_down_market(event, interval="5m", books={}, now=datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc))

    assert event["seriesSlug"] == "btc-up-or-down-5m"
    assert event["markets"][0]["conditionId"] == "0xabc"
    assert market.outcome_quotes[0].name == "Up"
    assert market.outcome_quotes[0].token_id == "up-token"


def test_sdk_order_book_adapter_keeps_asset_id_and_epoch_ms() -> None:
    book = sdk_order_book_to_clob_dict(
        {
            "market": "0xabc",
            "token_id": "up-token",
            "timestamp": "2026-06-13T05:06:00Z",
            "bids": [{"price": "0.47", "size": "10"}],
            "asks": [{"price": "0.49", "size": "7"}],
            "last_trade_price": "0.48",
        }
    )

    assert book["asset_id"] == "up-token"
    assert book["timestamp"] == "1781327160000"


def test_sdk_series_adapter_expands_events_and_keeps_metrics() -> None:
    events = sdk_series_to_gamma_events(
        {
            "slug": "btc-up-or-down-5m",
            "events": [
                {
                    "id": "event-1",
                    "slug": "btc-updown-5m-1",
                    "title": "Bitcoin Up or Down - June 13",
                    "endDate": "2026-06-13T05:10:00Z",
                    "markets": [
                        {
                            "id": "market-1",
                            "slug": "btc-updown-5m-1",
                            "condition_id": "0xabc",
                            "question": "Bitcoin Up or Down - June 13",
                            "state": {
                                "start_date": "2026-06-13T05:05:00Z",
                                "end_date": "2026-06-13T05:10:00Z",
                                "accepting_orders": True,
                            },
                            "volume": "123.45",
                            "liquidity": "678.90",
                            "outcomes": {
                                "yes": {"label": "Up", "token_id": "up-token", "price": "0.48"},
                                "no": {"label": "Down", "token_id": "down-token", "price": "0.52"},
                            },
                        }
                    ],
                }
            ],
        },
        series_slug="btc-up-or-down-5m",
    )

    market = normalize_up_down_market(
        events[0],
        interval="5m",
        books={},
        now=datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc),
    )

    assert events[0]["seriesSlug"] == "btc-up-or-down-5m"
    assert str(market.volume) == "123.45"
    assert str(market.liquidity) == "678.90"


def test_up_down_market_uses_event_metrics_when_market_metrics_are_empty() -> None:
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    event = make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z")
    event["volume"] = "321.00"
    event["liquidity"] = "654.00"
    event["markets"][0]["volumeNum"] = None
    event["markets"][0]["liquidityNum"] = None

    market = normalize_up_down_market(event, interval="5m", books={}, now=now)

    assert str(market.volume) == "321.00"
    assert str(market.liquidity) == "654.00"


@pytest.mark.asyncio
async def test_series_fetch_filters_and_sorts_btc_events(monkeypatch) -> None:
    async def fake_fetch_series_events(self, *, series_slug):
        return [
            make_event("btc-updown-5m-3", "2026-06-13T05:15:00Z", "2026-06-13T05:20:00Z"),
            make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
            make_event("btc-updown-5m-old", "2026-06-13T04:55:00Z", "2026-06-13T05:00:00Z"),
            {"slug": "eth-updown-5m-1", "seriesSlug": "eth-up-or-down-5m", "endDate": "2026-06-13T05:10:00Z"},
        ]

    monkeypatch.setattr(PolymarketClient, "_fetch_series_events", fake_fetch_series_events)

    events = await PolymarketClient(gamma_base_url="https://example.invalid").fetch_up_down_series_events(
        interval="5m",
        series_slug="btc-up-or-down-5m",
        end_date_min=datetime(2026, 6, 13, 5, 5, tzinfo=timezone.utc),
        limit=2,
    )

    assert [event["slug"] for event in events] == ["btc-updown-5m-1", "btc-updown-5m-3"]


@pytest.mark.asyncio
async def test_series_fetch_hydrates_missing_event_markets(monkeypatch) -> None:
    async def fake_fetch_series_events(self, *, series_slug):
        return [
            {
                "id": "event-1",
                "slug": "btc-updown-15m-1",
                "title": "Bitcoin Up or Down - June 13",
                "seriesSlug": series_slug,
                "startTime": "2026-06-13T05:00:00Z",
                "endDate": "2026-06-13T05:15:00Z",
                "liquidity": "1000",
                "markets": [],
            }
        ]

    async def fake_hydrate_events_missing_markets(self, events):
        return [
            {
                **events[0],
                "markets": [
                    {
                        "id": "market-1",
                        "question": "Bitcoin Up or Down - June 13",
                        "conditionId": "0xabc",
                        "slug": "btc-updown-15m-1",
                        "eventStartTime": "2026-06-13T05:00:00Z",
                        "endDate": "2026-06-13T05:15:00Z",
                        "outcomes": '["Up", "Down"]',
                        "outcomePrices": '["0.48", "0.52"]',
                        "clobTokenIds": '["up-token", "down-token"]',
                        "acceptingOrders": True,
                    }
                ],
            }
        ]

    monkeypatch.setattr(PolymarketClient, "_fetch_series_events", fake_fetch_series_events)
    monkeypatch.setattr(PolymarketClient, "_hydrate_events_missing_markets", fake_hydrate_events_missing_markets)

    events = await PolymarketClient(gamma_base_url="https://example.invalid").fetch_up_down_series_events(
        interval="15m",
        series_slug="btc-up-or-down-15m",
        end_date_min=datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc),
        limit=2,
    )

    market = normalize_up_down_market(
        events[0],
        interval="15m",
        books={},
        now=datetime(2026, 6, 13, 5, 1, tzinfo=timezone.utc),
    )

    assert [quote.token_id for quote in market.outcome_quotes] == ["up-token", "down-token"]
    assert str(market.liquidity) == "1000"


@pytest.mark.asyncio
async def test_up_down_events_logs_retryable_series_fallback_without_warning_traceback(monkeypatch, caplog) -> None:
    async def fake_fetch_up_down_series_events(self, **kwargs):
        raise TransportError("Request failed")

    async def fake_fetch_event_page_with_sdk(self, client, **params):
        return [make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z")]

    monkeypatch.setattr(PolymarketClient, "fetch_up_down_series_events", fake_fetch_up_down_series_events)
    monkeypatch.setattr(PolymarketClient, "_fetch_event_page_with_sdk", fake_fetch_event_page_with_sdk)

    with caplog.at_level(logging.INFO, logger="app.services.polymarket_client"):
        events = await PolymarketClient(gamma_base_url="https://example.invalid").fetch_up_down_events(
            interval="5m",
            now=datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc),
            limit=2,
        )

    fallback_records = [
        record
        for record in caplog.records
        if record.message == "Polymarket up/down series fetch failed; falling back to events"
    ]
    assert [event["slug"] for event in events] == ["btc-updown-5m-1"]
    assert fallback_records
    assert all(record.levelno == logging.INFO for record in fallback_records)
    assert all(record.exc_info is None for record in fallback_records)


def test_sdk_profile_and_activity_adapters_keep_legacy_keys() -> None:
    profile = sdk_profile_to_gamma_dict({"wallet": "0x1111111111111111111111111111111111111111", "name": "alice"})
    activity = sdk_activity_to_data_row(
        {
            "wallet": "0x1111111111111111111111111111111111111111",
            "transaction_hash": "0xabc",
            "condition_id": "0xcondition",
            "token_id": "up-token",
            "shares": "1",
            "amount": "0.48",
            "event_slug": "btc-up-or-down",
            "type": "TRADE",
        }
    )

    assert profile["proxyWallet"] == "0x1111111111111111111111111111111111111111"
    assert activity["proxyWallet"] == "0x1111111111111111111111111111111111111111"
    assert activity["transactionHash"] == "0xabc"
    assert activity["conditionId"] == "0xcondition"
    assert activity["asset"] == "up-token"
    assert activity["usdcSize"] == "0.48"


@pytest.mark.asyncio
async def test_btc_up_down_ws_market_list_uses_fixed_fetch_options(monkeypatch) -> None:
    calls: list[tuple[str, int, bool]] = []

    async def fake_fetch(self, interval, limit, include_recent_closed=True):
        calls.append((interval, limit, include_recent_closed))
        now = datetime.now(timezone.utc)
        return [
            normalize_up_down_market(
                make_event(
                    "btc-updown-5m-1",
                    (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    (now + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
                ),
                interval=interval,
                books={},
                now=now,
            )
        ]

    monkeypatch.setattr(PolymarketClient, "fetch_btc_up_down_markets", fake_fetch)
    monkeypatch.setattr(routes_polymarket, "polymarket_up_down_store", PolymarketUpDownStore())

    markets = await routes_polymarket.ensure_btc_up_down_markets("15m")

    assert calls == [("15m", 12, True)]
    assert markets[0].window == "current"
    assert markets[0].interval == "15m"
    assert markets[0].outcome_quotes[0].name == "Up"


def make_event(slug: str, start_time: str, end_time: str) -> dict:
    return {
        "id": slug,
        "slug": slug,
        "title": "Bitcoin Up or Down - June 13, 1:05AM-1:10AM ET",
        "seriesSlug": "btc-up-or-down-5m",
        "endDate": end_time,
        "markets": [
            {
                "id": f"{slug}-market",
                "question": "Bitcoin Up or Down - June 13, 1:05AM-1:10AM ET",
                "conditionId": "0xabc",
                "slug": slug,
                "eventStartTime": start_time,
                "endDate": end_time,
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["0.48", "0.52"]',
                "clobTokenIds": '["up-token", "down-token"]',
                "acceptingOrders": True,
                "volumeNum": 100,
                "liquidityNum": 200,
            }
        ],
    }
