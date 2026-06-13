from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.polymarket_client import (
    PolymarketClient,
    is_btc_up_down_event,
    normalize_up_down_market,
    select_up_down_windows,
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
