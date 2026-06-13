from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.api import routes_notifications
from app.db.session import get_session
from app.main import create_app
from app.schemas.candle import Candle, IndicatorPoint
from app.schemas.market_signal import SignalInput
from app.schemas.notification import NotificationDelivery, TelegramStatus
from app.schemas.signal import SignalRecord
from app.services import notifications
from app.services.signal_analysis import analyze_signal_input


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    return TestClient(app)


def test_telegram_status_endpoint(monkeypatch) -> None:
    async def fake_get_telegram_status(session):
        return TelegramStatus(
            configured=True,
            enabled=True,
            chat_id_masked="12***89",
            missing=[],
            last_delivery=None,
        )

    monkeypatch.setattr(routes_notifications, "get_telegram_status", fake_get_telegram_status)

    response = make_client().get("/api/notifications/telegram/status")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["enabled"] is True
    assert response.json()["chat_id_masked"] == "12***89"


def test_patch_telegram_status_persists_enabled(monkeypatch) -> None:
    calls = {}

    async def fake_set_telegram_enabled(session, enabled):
        calls["enabled"] = enabled

    async def fake_get_telegram_status(session):
        return TelegramStatus(
            configured=True,
            enabled=calls["enabled"],
            chat_id_masked=None,
            missing=[],
            last_delivery=None,
        )

    monkeypatch.setattr(routes_notifications, "set_telegram_enabled", fake_set_telegram_enabled)
    monkeypatch.setattr(routes_notifications, "get_telegram_status", fake_get_telegram_status)

    response = make_client().patch("/api/notifications/telegram/status", json={"enabled": False})

    assert response.status_code == 200
    assert calls["enabled"] is False
    assert response.json()["enabled"] is False


def test_telegram_test_returns_config_error(monkeypatch) -> None:
    async def fake_send_test_message(session):
        raise ValueError("Missing Telegram config: telegram_bot_token")

    monkeypatch.setattr(routes_notifications, "send_test_message", fake_send_test_message)

    response = make_client().post("/api/notifications/telegram/test")

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing Telegram config: telegram_bot_token"


def test_analyze_signal_input_ignores_rsi_diff_before_candle_close(monkeypatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, is_closed=False)
    indicator = make_indicator(now, rsi=82, diff=9)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=50))

    assert [rule.key for rule in rules] == ["rsi_extreme_high"]


def test_analyze_signal_input_records_rsi_diff_after_candle_close(monkeypatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, is_closed=True)
    indicator = make_indicator(now, rsi=82, diff=13)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))

    assert [rule.key for rule in rules] == ["rsi_extreme_high", "rsi_ema_diff"]


@pytest.mark.parametrize(
    ("interval", "rsi", "expected_key", "expected_score"),
    [
        ("1m", 71, "rsi_high", 2),
        ("5m", 81, "rsi_extreme_high", 4),
        ("15m", 91, "rsi_super_high", 6),
        ("1h", 29, "rsi_low", 5),
        ("4h", 19, "rsi_extreme_low", 7),
        ("4h", 9, "rsi_super_low", 8),
    ],
)
def test_analyze_signal_input_scores_rsi_by_interval_and_threshold(
    interval,
    rsi,
    expected_key,
    expected_score,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, interval=interval, is_closed=True)
    indicator = make_indicator(now, interval=interval, rsi=rsi, diff=1)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))

    assert [rule.key for rule in rules] == [expected_key]
    assert rules[0].score == expected_score
    assert rules[0].action == ("sell" if "high" in expected_key else "buy")
    assert rules[0].direction == ("short" if "high" in expected_key else "long")
    assert rules[0].metadata["rsi"] == rsi
    assert rules[0].metadata["interval"] == interval


@pytest.mark.parametrize("rsi", [70, 80, 90, 30, 20, 10])
def test_analyze_signal_input_keeps_rsi_thresholds_strict(rsi) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, is_closed=True)
    indicator = make_indicator(now, rsi=rsi, diff=1)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))

    if rsi == 80:
        assert [rule.key for rule in rules] == ["rsi_high"]
    elif rsi == 90:
        assert [rule.key for rule in rules] == ["rsi_extreme_high"]
    elif rsi == 20:
        assert [rule.key for rule in rules] == ["rsi_low"]
    elif rsi == 10:
        assert [rule.key for rule in rules] == ["rsi_extreme_low"]
    else:
        assert rules == []


@pytest.mark.parametrize(
    ("interval", "diff", "expected_score"),
    [
        ("1m", 13, 1),
        ("5m", 16, 3),
        ("15m", 20, 5),
        ("1h", 20, 6),
        ("4h", 20, 7),
    ],
)
def test_analyze_signal_input_scores_rsi_diff_by_interval_and_threshold(
    interval,
    diff,
    expected_score,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, interval=interval, is_closed=True)
    indicator = make_indicator(now, interval=interval, rsi=50, diff=diff)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))
    diff_rule = next(rule for rule in rules if rule.key == "rsi_ema_diff")

    assert diff_rule.score == expected_score
    assert diff_rule.action == "sell"
    assert diff_rule.direction == "short"
    assert diff_rule.metadata["diff"] == diff
    assert diff_rule.metadata["abs_diff"] == abs(diff)
    assert diff_rule.metadata["interval"] == interval


def test_analyze_signal_input_ignores_unconfigured_interval_for_rsi_and_diff() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, interval="30m", is_closed=True)
    indicator = make_indicator(now, interval="30m", rsi=91, diff=20)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))

    assert rules == []


def test_analyze_signal_input_ignores_rsi_diff_at_or_below_min_threshold() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, is_closed=True)
    indicator = make_indicator(now, rsi=50, diff=12)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))

    assert [rule.key for rule in rules] == []


def test_analyze_signal_input_marks_negative_rsi_diff_as_buy_long() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(now, is_closed=True)
    indicator = make_indicator(now, rsi=50, diff=-13)
    signal_input = SignalInput(candle=candle, indicator=indicator)

    rules = analyze_signal_input(signal_input, now=now + timedelta(seconds=70))
    diff_rule = next(rule for rule in rules if rule.key == "rsi_ema_diff")

    assert diff_rule.action == "buy"
    assert diff_rule.direction == "long"


@pytest.mark.asyncio
async def test_process_signal_notifications_groups_signals(monkeypatch) -> None:
    calls = []
    candle_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signals = [
        make_signal_record(1, "rsi_low", "RSI <= 30", candle_time),
        make_signal_record(2, "rsi_ema_diff", "|RSI-EMA diff| >= 8", candle_time),
    ]

    async def fake_process_telegram_delivery(session, signal_records):
        calls.append(signal_records)
        return None

    monkeypatch.setattr(notifications, "process_telegram_delivery", fake_process_telegram_delivery)

    await notifications.process_signal_notifications(object(), signals)

    assert calls == [signals]


@pytest.mark.asyncio
async def test_disabled_signal_is_recorded_without_send(monkeypatch) -> None:
    calls = {}
    candle_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signals = [make_signal_record(1, "rsi_low", "RSI <= 30", candle_time)]

    async def fake_get_telegram_enabled(session):
        return False

    async def fake_send_telegram_message(message):
        calls["sent"] = message

    async def fake_insert_notification_delivery(session, **kwargs):
        calls["delivery"] = kwargs
        return (
            NotificationDelivery(
                id=1,
                channel=kwargs["channel"],
                delivery_key=kwargs["delivery_key"],
                target_type=kwargs["target_type"],
                target_key=kwargs["target_key"],
                status=kwargs["status"],
                title=kwargs["title"],
                message=kwargs["message"],
                error=kwargs["error"],
                sent_at=None,
                created_at=candle_time,
                updated_at=candle_time,
                signals=signals,
            ),
            True,
        )

    async def fake_record_service_event(session, **kwargs):
        calls["event"] = kwargs

    monkeypatch.setattr(notifications, "get_telegram_enabled", fake_get_telegram_enabled)
    monkeypatch.setattr(notifications, "send_telegram_message", fake_send_telegram_message)
    monkeypatch.setattr(
        notifications, "insert_notification_delivery", fake_insert_notification_delivery
    )
    monkeypatch.setattr(notifications, "record_service_event", fake_record_service_event)

    delivery = await notifications.process_telegram_delivery(object(), signals)

    assert delivery is not None
    assert calls["delivery"]["status"] == "skipped_disabled"
    assert calls["delivery"]["signals"] == signals
    assert "sent" not in calls


@pytest.mark.asyncio
async def test_telegram_delivery_sends_once_for_multiple_signals(monkeypatch) -> None:
    calls = {"sent": []}
    candle_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signals = [
        make_signal_record(1, "rsi_extreme_high", "RSI > 80", candle_time),
        make_signal_record(2, "rsi_ema_diff", "RSI-EMA diff = 13", candle_time),
    ]

    async def fake_get_telegram_enabled(session):
        return True

    async def fake_insert_notification_delivery(session, **kwargs):
        calls["delivery"] = kwargs
        return (
            NotificationDelivery(
                id=1,
                channel=kwargs["channel"],
                delivery_key=kwargs["delivery_key"],
                target_type=kwargs["target_type"],
                target_key=kwargs["target_key"],
                status=kwargs["status"],
                title=kwargs["title"],
                message=kwargs["message"],
                error=kwargs["error"],
                sent_at=None,
                created_at=candle_time,
                updated_at=candle_time,
                signals=signals,
            ),
            True,
        )

    async def fake_send_telegram_message(message):
        calls["sent"].append(message)

    async def fake_update_delivery_status(session, delivery_id, *, status, error, sent_at):
        return NotificationDelivery(
            id=delivery_id,
            channel="telegram",
            delivery_key=calls["delivery"]["delivery_key"],
            target_type=calls["delivery"]["target_type"],
            target_key=calls["delivery"]["target_key"],
            status=status,
            title=calls["delivery"]["title"],
            message=calls["delivery"]["message"],
            error=error,
            sent_at=sent_at,
            created_at=candle_time,
            updated_at=candle_time,
            signals=signals,
        )

    async def fake_record_service_event(session, **kwargs):
        calls["event"] = kwargs

    monkeypatch.setattr(notifications, "get_telegram_enabled", fake_get_telegram_enabled)
    monkeypatch.setattr(notifications, "telegram_config_state", lambda: (True, []))
    monkeypatch.setattr(
        notifications, "insert_notification_delivery", fake_insert_notification_delivery
    )
    monkeypatch.setattr(notifications, "send_telegram_message", fake_send_telegram_message)
    monkeypatch.setattr(notifications, "update_delivery_status", fake_update_delivery_status)
    monkeypatch.setattr(notifications, "record_service_event", fake_record_service_event)

    delivery = await notifications.process_telegram_delivery(object(), signals)

    assert delivery is not None
    assert delivery.status == "sent"
    assert calls["delivery"]["signals"] == signals
    assert calls["delivery"]["delivery_key"] == f"telegram:{signals[0].dedupe_key}"
    assert len(calls["sent"]) == 1
    assert "Total score: 50.00 🚀🚀🚀" in calls["sent"][0]
    assert "🚀🚀🚀 Signal alert" in calls["sent"][0]
    assert "RSI > 80" in calls["sent"][0]
    assert "RSI-EMA diff = 13" in calls["sent"][0]


def make_candle(open_time: datetime, interval: str = "1m", is_closed: bool = True) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        interval=interval,  # type: ignore[arg-type]
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        is_closed=is_closed,
    )


def make_indicator(
    candle_time: datetime,
    rsi: float,
    diff: float,
    interval: str = "1m",
) -> IndicatorPoint:
    return IndicatorPoint(
        symbol="BTCUSDT",
        interval=interval,  # type: ignore[arg-type]
        candle_time=candle_time,
        rsi=rsi,
        rsi_ema=rsi - diff,
        rsi_ema_diff=diff,
    )


def make_signal_record(
    signal_id: int,
    signal_key: str,
    signal_label: str,
    occurred_at: datetime,
) -> SignalRecord:
    return SignalRecord(
        id=signal_id,
        signal_key=signal_key,
        signal_label=signal_label,
        action="buy" if "low" in signal_key else "sell",
        direction="long" if "low" in signal_key else "short",
        target_type="candle",
        target_key="BTCUSDT:1m",
        dedupe_key=f"candle:BTCUSDT:1m:{occurred_at.isoformat()}",
        occurred_at=occurred_at,
        score=25,
        input_snapshot={},
        metadata={},
        created_at=occurred_at,
    )
