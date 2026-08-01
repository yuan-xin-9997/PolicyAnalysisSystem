"""Authentication API routes."""

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from policy_analysis.auth.dependencies import (
    get_auth_service,
    require_csrf_session,
    require_user,
)
from policy_analysis.auth.service import AuthenticatedSession, AuthService, PublicUser

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    client_address = request.client.host if request.client is not None else ""
    result = service.login(payload.username, payload.password, client_address)
    response.set_cookie(
        key="session",
        value=result.token,
        httponly=True,
        secure=service.secure_cookie,
        samesite="lax",
        path="/",
    )
    return {"user": result.user.to_dict(), "csrf_token": result.csrf_token}


@router.get("/me")
def me(current_user: PublicUser = Depends(require_user)) -> dict[str, object]:
    return current_user.to_dict()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: AuthenticatedSession = Depends(require_csrf_session),
    service: AuthService = Depends(get_auth_service),
) -> None:
    service.logout(session.id)
    response.delete_cookie(
        key="session",
        path="/",
        secure=service.secure_cookie,
        httponly=True,
        samesite="lax",
    )
