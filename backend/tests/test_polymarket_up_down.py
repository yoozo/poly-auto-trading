from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.polymarket_client import (
    PolymarketClient,
    is_btc_up_down_event,
    normalize_up_down_market,
    select_up_down_windows,
    sdk_activity_to_data_row,
    sdk_event_to_gamma_dict,
    sdk_order_book_to_clob_dict,
    sdk_profile_to_gamma_dict,
)


def test_filters_btc_5m_series() -> None:
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-5m", "title": "x"}, interval="5m")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-15m", "title": "x"}, interval="15m")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-1h", "title": "x"}, interval="1h")
    assert is_btc_up_down_event({"seriesSlug": "btc-up-or-down-4h", "title": "x"}, interval="4h")
    assert not is_btc_up_down_event({"seriesSlug": "eth-up-or-down-5m", "title": "x"}, interval="5m")


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
        make_event("closed", "2026-06-13T05:00:00Z", "2026-06-13T05:05:00Z"),
        make_event("current", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
        make_event("next", "2026-06-13T05:10:00Z", "2026-06-13T05:15:00Z"),
    ]

    selected = select_up_down_windows(events, now=now, limit=3)

    assert [event["slug"] for event in selected] == ["closed", "current", "next"]


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


def test_btc_up_down_endpoint(monkeypatch) -> None:
    async def fake_fetch(self, interval, limit, include_recent_closed=True):
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

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    response = client.get("/api/polymarket/btc-up-down?interval=15m&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["window"] == "current"
    assert body[0]["interval"] == "15m"
    assert body[0]["outcome_quotes"][0]["name"] == "Up"


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
