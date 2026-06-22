from __future__ import annotations

import base64
import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import AppSetting, PolymarketCredential
from app.services.polymarket_credentials import (
    PolymarketCredentialError,
    get_active_credential_id,
    get_credential_secret,
    import_polymarket_credential,
    list_credential_profiles,
    parse_import_payload,
    parse_import_payload_base64,
    resolve_runtime_credentials,
)
from app.services.polymarket_credential_validation import validate_polymarket_credential_import
import app.services.polymarket_credentials as credentials_service

SIGNER = "0x0000000000000000000000000000000000000001"
FUNDER = "0x0000000000000000000000000000000000000002"


def test_parse_import_payload_base64_validates_shape() -> None:
    payload = {
        "label": "Main",
        "signer_address": SIGNER,
        "funder_address": FUNDER,
        "api_key": "key-1234567890",
        "api_secret": "secret-value",
        "api_passphrase": "passphrase-value",
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    parsed = parse_import_payload_base64(encoded)

    assert parsed.label == "Main"
    assert parsed.signer_address == SIGNER
    assert parsed.funder_address == FUNDER
    assert parsed.signature_type == 3


def test_parse_import_payload_rejects_invalid_wallet() -> None:
    with pytest.raises(PolymarketCredentialError, match="signer_address"):
        parse_import_payload(
            {
                "signer_address": "not-a-wallet",
                "funder_address": FUNDER,
                "api_key": "key",
                "api_secret": "secret",
                "api_passphrase": "pass",
            }
        )


@pytest.mark.asyncio
async def test_validate_polymarket_credential_import_calls_balance_and_positions() -> None:
    client = FakeValidationClient()

    await validate_polymarket_credential_import(sample_import(), client=client)

    assert client.balance_signature_type == 3
    assert client.balance_signer == SIGNER
    assert client.positions_wallet == FUNDER


@pytest.mark.asyncio
async def test_validate_polymarket_credential_import_rejects_balance_failure() -> None:
    client = FakeValidationClient(balance_error=RuntimeError("secret-value should not leak"))

    with pytest.raises(PolymarketCredentialError, match="balance validation failed: RuntimeError"):
        await validate_polymarket_credential_import(sample_import(), client=client)


@pytest.mark.asyncio
async def test_validate_polymarket_credential_import_rejects_positions_failure() -> None:
    client = FakeValidationClient(positions_error=RuntimeError("positions failed"))

    with pytest.raises(PolymarketCredentialError, match="funder positions validation failed: RuntimeError"):
        await validate_polymarket_credential_import(sample_import(), client=client)


@pytest.mark.asyncio
async def test_import_requires_credentials_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "polymarket_credentials_encryption_key", "")
    sessionmaker = await make_sessionmaker()
    async with sessionmaker() as session:
        with pytest.raises(PolymarketCredentialError, match="ENCRYPTION_KEY"):
            await import_polymarket_credential(session, sample_import())


@pytest.mark.asyncio
async def test_import_encrypts_credentials_and_sets_first_profile_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "polymarket_credentials_encryption_key",
        Fernet.generate_key().decode("utf-8"),
    )
    sessionmaker = await make_sessionmaker()

    async with sessionmaker() as session:
        async with session.begin():
            profile = await import_polymarket_credential(session, sample_import())

    async with sessionmaker() as session:
        stored = await session.scalar(select(PolymarketCredential))
        assert stored is not None
        assert stored.api_key_encrypted != "key-1234567890"
        assert stored.api_secret_encrypted != "secret-value"
        assert stored.api_passphrase_encrypted != "passphrase-value"
        assert "secret-value" not in stored.api_secret_encrypted
        assert await get_active_credential_id(session) == profile.id

        secret = await get_credential_secret(session, profile.id)
        assert secret is not None
        assert secret.api_key == "key-1234567890"
        assert secret.api_secret == "secret-value"
        assert secret.api_passphrase == "passphrase-value"

        profiles = await list_credential_profiles(session)
        assert profiles[0].active is True
        assert profiles[0].api_key_masked == "key-12...7890"


@pytest.mark.asyncio
async def test_resolve_runtime_credentials_uses_active_db_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "polymarket_credentials_encryption_key",
        Fernet.generate_key().decode("utf-8"),
    )
    sessionmaker = await make_sessionmaker()
    monkeypatch.setattr(credentials_service, "AsyncSessionLocal", sessionmaker)

    async with sessionmaker() as session:
        async with session.begin():
            profile = await import_polymarket_credential(session, sample_import())

    runtime_credentials = await resolve_runtime_credentials()

    assert runtime_credentials is not None
    assert runtime_credentials.source == "db"
    assert runtime_credentials.credential_id == profile.id
    assert runtime_credentials.signer_address == SIGNER
    assert runtime_credentials.funder_address == FUNDER
    assert runtime_credentials.api_secret == "secret-value"


@pytest.mark.asyncio
async def test_resolve_runtime_credentials_returns_none_without_multi_wallet_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "polymarket_credentials_encryption_key", "")

    runtime_credentials = await resolve_runtime_credentials()

    assert runtime_credentials is None


async def make_sessionmaker() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AppSetting.__table__.create)
        await connection.run_sync(PolymarketCredential.__table__.create)
    return async_sessionmaker(engine, expire_on_commit=False)


def sample_import():
    return parse_import_payload(
        {
            "label": "Main",
            "signer_address": SIGNER,
            "funder_address": FUNDER,
            "signature_type": 3,
            "api_key": "key-1234567890",
            "api_secret": "secret-value",
            "api_passphrase": "passphrase-value",
        }
    )


class FakeValidationClient:
    def __init__(self, *, balance_error: Exception | None = None, positions_error: Exception | None = None) -> None:
        self.balance_error = balance_error
        self.positions_error = positions_error
        self.balance_signature_type: int | None = None
        self.balance_signer: str | None = None
        self.positions_wallet: str | None = None

    async def fetch_balance_allowance(self, *, credentials=None):
        if self.balance_error:
            raise self.balance_error
        self.balance_signature_type = credentials.signature_type
        self.balance_signer = credentials.signer_address
        return {"balance": "0", "allowances": {}}

    async def fetch_positions(self, wallet: str, size_threshold: float = 0):
        if self.positions_error:
            raise self.positions_error
        self.positions_wallet = wallet
        return []
