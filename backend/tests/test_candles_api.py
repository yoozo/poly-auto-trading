from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api import routes_candles
from app.db.session import get_session
from app.main import create_app
from app.schemas.candle import Candle


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
    return TestClient(app)


def test_candles_range_mode(monkeypatch) -> None:
    calls = {}
    fetched = [make_candle(0), make_candle(1)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] = kwargs
            return fetched

    async def fake_upsert(session, candles):
        calls["upserted"] = candles

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return fetched

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "upsert_candles", fake_upsert)
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
    assert calls["fetch"]["start_ms"] == 1767225600000
    assert calls["fetch"]["end_ms"] == 1767229200000
    assert calls["fetch"]["limit"] == 1000
    assert calls["upserted"] == fetched
    assert calls["between"]["symbol"] == "BTCUSDT"


def test_candles_range_mode_uses_database_cache_when_count_matches(monkeypatch) -> None:
    calls = {"fetch": 0}
    cached = [make_candle(index) for index in range(10)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] += 1
            return []

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return cached

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/candles?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767225600000&end_ms=1767226140000"
    )

    assert response.status_code == 200
    assert len(response.json()) == 10
    assert calls["fetch"] == 0
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


def test_candles_limit_mode_uses_fresh_database_cache(monkeypatch) -> None:
    calls = {"fetch": 0}
    cached = [make_candle(index) for index in range(300)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] += 1
            return []

    async def fake_list_candles(session, symbol, interval, limit):
        return cached[-limit:]

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)
    monkeypatch.setattr(routes_candles, "utc_now", lambda: datetime(2026, 1, 1, 4, 59, 30, tzinfo=timezone.utc))

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    assert len(response.json()) == 300
    assert calls["fetch"] == 0


def test_candles_limit_mode_fetches_when_database_cache_is_short(monkeypatch) -> None:
    calls = {"fetch": 0}
    cached = [make_candle(index) for index in range(10)]
    fetched = [make_candle(index) for index in range(300)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] += 1
            return fetched

    async def fake_list_candles(session, symbol, interval, limit):
        if calls["fetch"]:
            return fetched[-limit:]
        return cached

    async def fake_upsert(session, candles):
        calls["upserted"] = candles

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "list_candles", fake_list_candles)
    monkeypatch.setattr(routes_candles, "upsert_candles", fake_upsert)

    client = make_client()
    response = client.get("/api/candles?symbol=BTCUSDT&interval=1m&limit=300")

    assert response.status_code == 200
    assert len(response.json()) == 300
    assert calls["fetch"] == 1
    assert calls["upserted"] == fetched


def test_indicators_range_mode_uses_warmup_and_filters_response(monkeypatch) -> None:
    calls = {}
    fetched = [make_candle(index) for index in range(100)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] = kwargs
            return fetched

    async def fake_upsert(session, candles):
        calls["upserted"] = candles

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return fetched

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "upsert_candles", fake_upsert)
    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/indicators?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767228000000&end_ms=1767230400000"
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["candle_time"] == "2026-01-01T00:40:00Z"
    assert body[-1]["candle_time"] == "2026-01-01T01:20:00Z"
    assert calls["fetch"]["start_ms"] == 1767223200000
    assert calls["fetch"]["end_ms"] == 1767230400000
    assert calls["upserted"] == fetched
    assert calls["between"]["symbol"] == "BTCUSDT"


def test_indicators_range_mode_uses_database_cache_when_count_matches(monkeypatch) -> None:
    calls = {"fetch": 0}
    cached = [make_candle(index) for index in range(91)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls["fetch"] += 1
            return []

    async def fake_list_between(session, symbol, interval, start, end):
        calls["between"] = {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
        }
        return cached

    monkeypatch.setattr(routes_candles, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(routes_candles, "list_candles_between", fake_list_between)

    client = make_client()
    response = client.get(
        "/api/indicators?symbol=BTCUSDT&interval=1m&limit=1000&start_ms=1767230400000&end_ms=1767231000000"
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 11
    assert body[0]["candle_time"] == "2026-01-01T01:20:00Z"
    assert body[-1]["candle_time"] == "2026-01-01T01:30:00Z"
    assert calls["fetch"] == 0
    assert calls["between"]["symbol"] == "BTCUSDT"


def test_indicators_range_requires_start_and_end() -> None:
    client = make_client()
    response = client.get("/api/indicators?interval=1m&start_ms=1767225600000")
    assert response.status_code == 400
