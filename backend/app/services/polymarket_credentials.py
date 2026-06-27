from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AppSetting, PolymarketCredential
from app.db.session import AsyncSessionLocal

ACTIVE_CREDENTIAL_SETTING_KEY = "polymarket.active_credential_id"
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class PolymarketCredentialError(ValueError):
    pass


@dataclass(frozen=True)
class PolymarketCredentialImport:
    label: str
    signer_address: str
    funder_address: str
    signature_type: int
    api_key: str
    api_secret: str
    api_passphrase: str


@dataclass(frozen=True)
class PolymarketCredentialProfile:
    id: str
    label: str
    signer_address: str
    funder_address: str
    signature_type: int
    api_key_masked: str
    active: bool = False


@dataclass(frozen=True)
class PolymarketCredentialSecret:
    id: str
    label: str
    signer_address: str
    funder_address: str
    signature_type: int
    api_key: str
    api_secret: str
    api_passphrase: str


@dataclass(frozen=True)
class RuntimePolymarketCredentials:
    source: str
    credential_id: str | None
    signer_address: str
    funder_address: str
    signature_type: int
    api_key: str
    api_secret: str
    api_passphrase: str


def fernet_from_settings() -> Fernet:
    return fernet_from_key(settings.polymarket_credentials_encryption_key)


def fernet_from_key(raw_key: str) -> Fernet:
    key = raw_key.strip()
    if not key:
        raise PolymarketCredentialError("POLYMARKET_CREDENTIALS_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise PolymarketCredentialError(
            "POLYMARKET_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc


def encrypt_secret(value: str, *, fernet: Fernet | None = None) -> str:
    secret = value.strip()
    if not secret:
        raise PolymarketCredentialError("CLOB credential fields cannot be empty")
    cipher = fernet or fernet_from_settings()
    return cipher.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str, *, fernet: Fernet | None = None) -> str:
    cipher = fernet or fernet_from_settings()
    try:
        return cipher.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise PolymarketCredentialError("Stored Polymarket credential cannot be decrypted") from exc


def parse_import_payload(payload: dict[str, Any]) -> PolymarketCredentialImport:
    signer_address = normalized_wallet(payload.get("signer_address"), field_name="signer_address")
    funder_address = normalized_wallet(payload.get("funder_address"), field_name="funder_address")
    signature_type = int(payload.get("signature_type") or 3)
    if signature_type not in {0, 1, 2, 3}:
        raise PolymarketCredentialError("signature_type must be one of 0, 1, 2, 3")

    api_key = required_string(payload.get("api_key"), "api_key")
    api_secret = required_string(payload.get("api_secret"), "api_secret")
    api_passphrase = required_string(payload.get("api_passphrase"), "api_passphrase")
    label = str(payload.get("label") or "").strip() or short_wallet_label(signer_address)

    return PolymarketCredentialImport(
        label=label[:120],
        signer_address=signer_address,
        funder_address=funder_address,
        signature_type=signature_type,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )


def parse_import_payload_base64(encoded_payload: str) -> PolymarketCredentialImport:
    value = encoded_payload.strip()
    if not value:
        raise PolymarketCredentialError("POLY_CREDENTIAL_PAYLOAD is required")
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolymarketCredentialError("POLY_CREDENTIAL_PAYLOAD must be base64 encoded JSON") from exc
    if not isinstance(payload, dict):
        raise PolymarketCredentialError("POLY_CREDENTIAL_PAYLOAD JSON must be an object")
    return parse_import_payload(payload)


async def import_polymarket_credential(
    session: AsyncSession,
    payload: PolymarketCredentialImport,
    *,
    activate_if_first: bool = True,
) -> PolymarketCredentialProfile:
    fernet = fernet_from_settings()
    existing = await session.scalar(
        select(PolymarketCredential).where(
            PolymarketCredential.signer_address == payload.signer_address,
            PolymarketCredential.funder_address == payload.funder_address,
        )
    )
    if existing is None:
        credential = PolymarketCredential(
            id=str(uuid4()),
            signer_address=payload.signer_address,
            funder_address=payload.funder_address,
        )
        session.add(credential)
    else:
        credential = existing

    credential.label = payload.label
    credential.signature_type = payload.signature_type
    # CLOB API key 也按 secret 处理；API 层只暴露 masked 版本，避免以后误用明文。
    credential.api_key_encrypted = encrypt_secret(payload.api_key, fernet=fernet)
    credential.api_secret_encrypted = encrypt_secret(payload.api_secret, fernet=fernet)
    credential.api_passphrase_encrypted = encrypt_secret(payload.api_passphrase, fernet=fernet)
    await session.flush()

    active_id = await get_active_credential_id(session)
    if activate_if_first and active_id is None:
        await set_active_credential_id(session, credential.id)
        active_id = credential.id
    return profile_from_model(credential, active_id=active_id, fernet=fernet)


async def get_active_credential_id(session: AsyncSession) -> str | None:
    setting = await session.get(AppSetting, ACTIVE_CREDENTIAL_SETTING_KEY)
    if setting is None:
        return None
    credential_id = (setting.value or {}).get("credential_id")
    return str(credential_id) if credential_id else None


async def set_active_credential_id(session: AsyncSession, credential_id: str) -> None:
    credential = await session.get(PolymarketCredential, credential_id)
    if credential is None:
        raise PolymarketCredentialError("Polymarket credential profile not found")
    setting = await session.get(AppSetting, ACTIVE_CREDENTIAL_SETTING_KEY)
    if setting is None:
        setting = AppSetting(key=ACTIVE_CREDENTIAL_SETTING_KEY, value={})
        session.add(setting)
    setting.value = {"credential_id": credential_id}
    await session.flush()


async def list_credential_profiles(session: AsyncSession) -> list[PolymarketCredentialProfile]:
    active_id = await get_active_credential_id(session)
    result = await session.scalars(select(PolymarketCredential).order_by(PolymarketCredential.created_at))
    fernet = fernet_from_settings()
    return [profile_from_model(row, active_id=active_id, fernet=fernet) for row in result]


async def update_credential_label(
    session: AsyncSession,
    credential_id: str,
    label: str,
) -> PolymarketCredentialProfile:
    credential = await session.get(PolymarketCredential, credential_id)
    if credential is None:
        raise PolymarketCredentialError("Polymarket credential profile not found")
    normalized_label = label.strip()
    if not normalized_label:
        raise PolymarketCredentialError("label cannot be empty")
    credential.label = normalized_label[:120]
    await session.flush()
    active_id = await get_active_credential_id(session)
    return profile_from_model(credential, active_id=active_id, fernet=fernet_from_settings())


async def delete_credential_profile(session: AsyncSession, credential_id: str) -> None:
    active_id = await get_active_credential_id(session)
    if active_id == credential_id:
        raise PolymarketCredentialError("Active Polymarket credential profile cannot be deleted")
    credential = await session.get(PolymarketCredential, credential_id)
    if credential is None:
        raise PolymarketCredentialError("Polymarket credential profile not found")
    await session.delete(credential)
    await session.flush()


async def get_credential_secret(
    session: AsyncSession,
    credential_id: str,
) -> PolymarketCredentialSecret | None:
    credential = await session.get(PolymarketCredential, credential_id)
    if credential is None:
        return None
    fernet = fernet_from_settings()
    return PolymarketCredentialSecret(
        id=credential.id,
        label=credential.label,
        signer_address=credential.signer_address,
        funder_address=credential.funder_address,
        signature_type=credential.signature_type,
        api_key=decrypt_secret(credential.api_key_encrypted, fernet=fernet),
        api_secret=decrypt_secret(credential.api_secret_encrypted, fernet=fernet),
        api_passphrase=decrypt_secret(credential.api_passphrase_encrypted, fernet=fernet),
    )


async def resolve_runtime_credentials() -> RuntimePolymarketCredentials | None:
    if not settings.polymarket_credentials_encryption_key.strip():
        return None
    async with AsyncSessionLocal() as session:
        active_id = await get_active_credential_id(session)
        if not active_id:
            return None
        secret = await get_credential_secret(session, active_id)
        if secret is None:
            raise PolymarketCredentialError("Active Polymarket credential profile not found")
        return RuntimePolymarketCredentials(
            source="db",
            credential_id=secret.id,
            signer_address=secret.signer_address,
            funder_address=secret.funder_address,
            signature_type=secret.signature_type,
            api_key=secret.api_key,
            api_secret=secret.api_secret,
            api_passphrase=secret.api_passphrase,
        )


async def resolve_account_wallet() -> str | None:
    runtime_credentials = await resolve_runtime_credentials()
    if runtime_credentials is not None:
        return runtime_credentials.funder_address
    return None


def profile_from_model(
    credential: PolymarketCredential,
    *,
    active_id: str | None,
    fernet: Fernet,
) -> PolymarketCredentialProfile:
    return PolymarketCredentialProfile(
        id=credential.id,
        label=credential.label,
        signer_address=credential.signer_address,
        funder_address=credential.funder_address,
        signature_type=credential.signature_type,
        api_key_masked=mask_secret(decrypt_secret(credential.api_key_encrypted, fernet=fernet)),
        active=credential.id == active_id,
    )


def profile_to_safe_dict(profile: PolymarketCredentialProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "label": profile.label,
        "signer_address": profile.signer_address,
        "funder_address": profile.funder_address,
        "signature_type": profile.signature_type,
        "api_key_masked": profile.api_key_masked,
        "active": profile.active,
    }


def credentials_encryption_configured() -> bool:
    return bool(settings.polymarket_credentials_encryption_key.strip())


def normalized_wallet(value: Any, *, field_name: str) -> str:
    wallet = str(value or "").strip().lower()
    if not WALLET_RE.fullmatch(wallet):
        raise PolymarketCredentialError(f"{field_name} must be a valid wallet address")
    return wallet


def required_string(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PolymarketCredentialError(f"{field_name} is required")
    return text


def mask_secret(value: str) -> str:
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def short_wallet_label(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}"
