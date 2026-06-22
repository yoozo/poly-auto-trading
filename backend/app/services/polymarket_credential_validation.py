from __future__ import annotations

from typing import Protocol

import httpx

from app.services.polymarket_client import PolymarketClient, PolymarketInputError
from app.services.polymarket_credentials import (
    PolymarketCredentialError,
    PolymarketCredentialImport,
    RuntimePolymarketCredentials,
)


class PolymarketCredentialValidationClient(Protocol):
    async def fetch_balance_allowance(
        self,
        *,
        credentials: RuntimePolymarketCredentials | None = None,
    ) -> object: ...

    async def fetch_positions(self, wallet: str, size_threshold: float = 0) -> object: ...


async def validate_polymarket_credential_import(
    payload: PolymarketCredentialImport,
    *,
    client: PolymarketCredentialValidationClient | None = None,
) -> None:
    validation_client = client or PolymarketClient()
    runtime_credentials = RuntimePolymarketCredentials(
        source="import",
        credential_id=None,
        signer_address=payload.signer_address,
        funder_address=payload.funder_address,
        signature_type=payload.signature_type,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        api_passphrase=payload.api_passphrase,
    )
    try:
        await validation_client.fetch_balance_allowance(credentials=runtime_credentials)
    except Exception as exc:
        raise PolymarketCredentialError(f"balance validation failed: {safe_error_message(exc)}") from exc

    try:
        await validation_client.fetch_positions(payload.funder_address, size_threshold=0)
    except Exception as exc:
        raise PolymarketCredentialError(f"funder positions validation failed: {safe_error_message(exc)}") from exc


def safe_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return f"HTTP {response.status_code} {response.reason_phrase}".strip()
    if isinstance(exc, PolymarketInputError):
        return str(exc)
    return exc.__class__.__name__
