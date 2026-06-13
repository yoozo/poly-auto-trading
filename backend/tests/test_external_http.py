import httpx
import pytest

from app.services.external_http import RetryPolicy, with_retry


@pytest.mark.asyncio
async def test_with_retry_retries_retryable_errors(monkeypatch) -> None:
    calls = 0

    async def fake_sleep(_delay: float) -> None:
        return None

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.TimeoutException("timeout")
        return "ok"

    monkeypatch.setattr("app.services.external_http.asyncio.sleep", fake_sleep)

    result = await with_retry(operation, policy=RetryPolicy(attempts=3, base_delay=0.01))

    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_with_retry_raises_final_error(monkeypatch) -> None:
    calls = 0

    async def fake_sleep(_delay: float) -> None:
        return None

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("app.services.external_http.asyncio.sleep", fake_sleep)

    with pytest.raises(httpx.ConnectError):
        await with_retry(operation, policy=RetryPolicy(attempts=2, base_delay=0.01))

    assert calls == 2
