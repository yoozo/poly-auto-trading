from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes_candles
from app.db.session import get_session
from app.main import create_app
from app.schemas.candle import Candle
from app.services.candle_backfill import CandleBackfillStatus
from app.services.indicator_backfill import IndicatorBackfillStatus
from conftest import login_test_client


class NoopCandleSyncService:
    async def ensure_range(self, session, *, symbol, interval, start_ms, end_ms):
        return None

    async def ensure_latest_window(self, session, *, symbol, interval, limit):
        return None


class EmptyMarketSignalPipeline:
    def get_live_candles(self, symbol, interval, limit=None):  # noqa: ANN001
        return []

    def latest_market_payload(self, symbol, interval):  # noqa: ANN001
        return None


@pytest.fixture(autouse=True)
def noop_candle_sync(monkeypatch):
    monkeypatch.setattr(routes_candles, "candle_sync_service", NoopCandleSyncService())
    monkeypatch.setattr(routes_candles, "market_signal_pipeline", EmptyMarketSignalPipeline())


def make_candle(index: int) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=100 + index,
        high=101 + index,
        low=99 + index,
        close=100 + index,
        volume=1,
        is_closed=True,
    )


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app)
    login_test_client(client)
    return client


def test_candles_range_mode_syncs_then_reads_database(monkeypatch) -> None:
    calls = {}
    cached = [make_candle(0), make_candle(1)]

    class FakeCandleSyncService:
        async def ensure_range(self, session, *, symbol, interval, start_ms, end_ms):
            calls["sync"] = {
                "symbol": symbol,
                "interval": interval,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return cached

    monkeypatch.setattr(routes_candles, "candle_sync_service", FakeCandleSyncService())
    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/candles?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767225600000&end_ms=1767229200000"
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["open_time"] for item in body] == [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:01:00Z",
    ]
    assert calls["sync"] == {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start_ms": 1767225600000,
        "end_ms": 1767229200000,
    }
    assert calls["between"]["symbol"] == "BTCUSDT"


def test_candles_range_mode_returns_partial_database_window(monkeypatch) -> None:
    calls = {"between": 0}
    cached = [make_candle(index) for index in range(3)]

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] += 1
        return cached

    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/candles?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767225600000&end_ms=1767285660000"
    )

    assert response.status_code == 200
    assert len(response.json()) == 3
    assert calls["between"] == 1


def test_candle_backfill_endpoint_starts_runner(monkeypatch) -> None:
    calls = {}
    status = CandleBackfillStatus(
        state="running",
        symbol="BTCUSDT",
        intervals=["1m", "5m"],
        fetched={"1m": 0, "5m": 0},
    )

    class FakeBackfillRunner:
        def status(self):
            return status

        async def start_all(self, *, symbol):
            calls["symbol"] = symbol
            return status

    monkeypatch.setattr(routes_candles, "candle_backfill_runner", FakeBackfillRunner())

    client = make_client()
    response = client.post("/api/candles/backfill?symbol=BTCUSDT")

    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert calls["symbol"] == "BTCUSDT"


def test_indicator_backfill_endpoint_starts_runner(monkeypatch) -> None:
    calls = {}
    status = IndicatorBackfillStatus(
        state="running",
        symbol="BTCUSDT",
        intervals=["1m", "5m"],
        total_inserted=0,
    )

    class FakeBackfillRunner:
        async def status(self):
            return status

        async def start_all(self, *, symbol):
            calls["symbol"] = symbol
            return status

    monkeypatch.setattr(routes_candles, "indicator_backfill_runner", FakeBackfillRunner())

    client = make_client()
    response = client.post("/api/indicators/backfill?symbol=BTCUSDT")

    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert calls["symbol"] == "BTCUSDT"


def test_candles_range_mode_does_not_require_full_count(monkeypatch) -> None:
    calls = {}
    cached = [make_candle(index) for index in range(10)]

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return cached

    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/candles?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767225600000&end_ms=1767226140000"
    )

    assert response.status_code == 200
    assert len(response.json()) == 10
    assert calls["between"]["symbol"] == "BTCUSDT"


def test_candles_range_requires_start_and_end() -> None:
    client = make_client()
    response = client.get("/api/candles?interval=1m&start_ms=1767225600000")
    assert response.status_code == 400


def test_candles_range_requires_ordered_bounds() -> None:
    client = make_client()
    response = client.get(
        "/api/candles?interval=1m&start_ms=1767229200000&end_ms=1767225600000"
    )
    assert response.status_code == 400


def test_candles_limit_mode_syncs_latest_window_before_reading(monkeypatch) -> None:
    calls = {}
    cached = [make_candle(index) for index in range(300)]

    class FakeCandleSyncService:
        async def ensure_latest_window(self, session, *, symbol, interval, limit):
            calls["sync"] = {"symbol": symbol, "interval": interval, "limit": limit}

    async def fake_list_candles(session, symbol, interval, limit):
        return cached[-limit:]

    monkeypatch.setattr(routes_candles, "candle_sync_service", FakeCandleSyncService())
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    assert len(response.json()) == 300
    assert calls["sync"] == {"symbol": "BTCUSDT", "interval": "1m", "limit": 300}


def test_candles_limit_mode_merges_live_open_candle(monkeypatch) -> None:
    cached = [make_candle(index) for index in range(300)]
    live_candle = make_candle(300).model_copy(update={"high": 556, "close": 555, "is_closed": False})

    async def fake_list_candles(session, symbol, interval, limit):
        return cached[-limit:]

    class FakeMarketSignalPipeline:
        def get_live_candles(self, symbol, interval, limit=None):  # noqa: ANN001
            return [live_candle]

    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)
    monkeypatch.setattr(routes_candles, "market_signal_pipeline", FakeMarketSignalPipeline())

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 300
    assert body[-1]["open_time"] == "2026-01-01T05:00:00Z"
    assert body[-1]["close"] == 555
    assert body[-1]["is_closed"] is False
    assert body[0]["open_time"] == "2026-01-01T00:01:00Z"


def test_candles_limit_mode_live_candle_replaces_same_open_time(monkeypatch) -> None:
    cached = [make_candle(index) for index in range(300)]
    live_candle = cached[-1].model_copy(update={"high": 778, "close": 777, "is_closed": False})

    async def fake_list_candles(session, symbol, interval, limit):
        return cached[-limit:]

    class FakeMarketSignalPipeline:
        def get_live_candles(self, symbol, interval, limit=None):  # noqa: ANN001
            return [live_candle]

    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)
    monkeypatch.setattr(routes_candles, "market_signal_pipeline", FakeMarketSignalPipeline())

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 300
    assert body[-1]["open_time"] == "2026-01-01T04:59:00Z"
    assert body[-1]["close"] == 777
    assert body[-1]["is_closed"] is False


def test_parse_market_subscribe_message_accepts_valid_interval() -> None:
    assert routes_candles.parse_market_subscribe_message('{"type":"market.subscribe","interval":"5m"}') == "5m"


def test_parse_market_subscribe_message_rejects_invalid_payload() -> None:
    assert routes_candles.parse_market_subscribe_message("not-json") is None
    assert routes_candles.parse_market_subscribe_message('{"type":"noop","interval":"5m"}') is None
    assert routes_candles.parse_market_subscribe_message('{"type":"market.subscribe","interval":"2m"}') is None


@pytest.mark.asyncio
async def test_initial_market_payload_prefers_live_window(monkeypatch) -> None:
    live_candle = make_candle(20).model_copy(update={"is_closed": False})
    calls = {"db": 0}

    class FakeMarketSignalPipeline:
        def latest_market_payload(self, symbol, interval):  # noqa: ANN001
            return {"type": "market.candle", "symbol": symbol, "interval": interval, "candle": {"open_time": "live"}}

        def market_payload_from_candles(self, symbol, interval, candles):  # noqa: ANN001
            return {"type": "market.candle", "symbol": symbol, "interval": interval, "candle": live_candle.model_dump(mode="json")}

    async def fake_list_candles(session, symbol, interval, limit):
        calls["db"] += 1
        return [make_candle(1)]

    monkeypatch.setattr(routes_candles, "market_signal_pipeline", FakeMarketSignalPipeline())
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)

    payload = await routes_candles.initial_market_payload("BTCUSDT", "1m")

    assert payload == {"type": "market.candle", "symbol": "BTCUSDT", "interval": "1m", "candle": {"open_time": "live"}}
    assert calls["db"] == 0


@pytest.mark.asyncio
async def test_initial_market_payload_falls_back_to_database_snapshot(monkeypatch) -> None:
    cached = [make_candle(index) for index in range(40)]

    class FakeSessionLocal:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakeMarketSignalPipeline:
        def latest_market_payload(self, symbol, interval):  # noqa: ANN001
            return None

        def market_payload_from_candles(self, symbol, interval, candles):  # noqa: ANN001
            return {
                "type": "market.candle",
                "symbol": symbol,
                "interval": interval,
                "candle": candles[-1].model_dump(mode="json"),
            }

    async def fake_list_candles(session, symbol, interval, limit):
        return cached[-limit:]

    monkeypatch.setattr(routes_candles, "AsyncSessionLocal", FakeSessionLocal)
    monkeypatch.setattr(routes_candles, "market_signal_pipeline", FakeMarketSignalPipeline())
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)

    payload = await routes_candles.initial_market_payload("BTCUSDT", "1m")

    assert payload is not None
    assert payload["type"] == "market.candle"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1m"
    assert payload["candle"]["open_time"] == "2026-01-01T00:39:00Z"  # type: ignore[index]
    assert set(payload) == {"type", "symbol", "interval", "candle"}


def test_candles_limit_mode_returns_short_database_window(monkeypatch) -> None:
    cached = [make_candle(index) for index in range(10)]

    async def fake_list_candles(session, symbol, interval, limit):
        return cached

    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    assert len(response.json()) == 10


def test_candles_limit_mode_returns_empty_after_sync_when_database_is_empty(monkeypatch) -> None:
    calls = {"sync": 0}

    async def fake_list_candles(session, symbol, interval, limit):
        return []

    class FakeCandleSyncService:
        async def ensure_latest_window(self, session, *, symbol, interval, limit):
            calls["sync"] += 1

    monkeypatch.setattr(routes_candles, "candle_sync_service", FakeCandleSyncService())
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    assert response.json() == []
    assert calls["sync"] == 1


def test_indicators_query_endpoint_removed() -> None:
    client = make_client()
    response = client.get("/api/indicators?interval=1m&start_ms=1767225600000")
    assert response.status_code == 404
