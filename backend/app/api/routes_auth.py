from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.core.auth import (
    auth_is_configured,
    clear_session_cookie,
    create_session_token,
    request_is_authenticated,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    authenticated: bool
    configured: bool


@router.post("/login", response_model=SessionResponse)
async def login(payload: LoginRequest, response: Response) -> SessionResponse:
    if not auth_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )
    if not verify_password(payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
    set_session_cookie(response, create_session_token())
    return SessionResponse(authenticated=True, configured=True)


@router.post("/logout", response_model=SessionResponse)
async def logout(response: Response) -> SessionResponse:
    clear_session_cookie(response)
    return SessionResponse(authenticated=False, configured=auth_is_configured())


@router.get("/session", response_model=SessionResponse)
async def session(request: Request) -> SessionResponse:
    return SessionResponse(
        authenticated=request_is_authenticated(request),
        configured=auth_is_configured(),
    )
