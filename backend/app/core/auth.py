import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, Request, Response, WebSocket, status
from starlette.websockets import WebSocketState

from app.core.config import settings


AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
    "/api/health",
}


def auth_is_configured() -> bool:
    return bool(settings.auth_password and settings.auth_session_secret)


def verify_password(password: str) -> bool:
    if not auth_is_configured():
        return False
    return hmac.compare_digest(password, settings.auth_password)


def create_session_token() -> str:
    if not auth_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )
    now = int(time.time())
    payload = {
        "sub": "owner",
        "iat": now,
        "exp": now + settings.auth_session_ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded_payload = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str | None) -> bool:
    if not token or not auth_is_configured():
        return False
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(encoded_payload), signature):
        return False
    try:
        payload = json.loads(_base64url_decode(encoded_payload))
    except (ValueError, json.JSONDecodeError):
        return False
    if payload.get("sub") != "owner":
        return False
    exp = payload.get("exp")
    return isinstance(exp, int) and exp > int(time.time())


def request_is_authenticated(request: Request) -> bool:
    return verify_session_token(request.cookies.get(settings.auth_cookie_name))


async def require_websocket_auth(websocket: WebSocket) -> bool:
    if verify_session_token(websocket.cookies.get(settings.auth_cookie_name)):
        return True
    # WebSocket 没有 HTTP middleware 兜底，必须在 accept 前单独拒绝，避免实时数据绕过认证。
    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return False


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _sign(encoded_payload: str) -> str:
    digest = hmac.new(
        settings.auth_session_secret.encode(),
        encoded_payload.encode(),
        hashlib.sha256,
    ).digest()
    return _base64url_encode(digest)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")
