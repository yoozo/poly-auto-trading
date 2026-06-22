from __future__ import annotations

import asyncio
import json
import os
import sys

from app.db.session import AsyncSessionLocal
from app.services.polymarket_credentials import (
    PolymarketCredentialError,
    import_polymarket_credential,
    parse_import_payload_base64,
    profile_to_safe_dict,
)
from app.services.polymarket_credential_validation import validate_polymarket_credential_import


async def run() -> int:
    try:
        payload = parse_import_payload_base64(os.environ.get("POLY_CREDENTIAL_PAYLOAD", ""))
        await validate_polymarket_credential_import(payload)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                profile = await import_polymarket_credential(session, payload)
        print(json.dumps({"ok": True, "profile": profile_to_safe_dict(profile)}, ensure_ascii=False))
        return 0
    except PolymarketCredentialError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
