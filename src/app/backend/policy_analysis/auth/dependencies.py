"""FastAPI dependencies for authenticated requests."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from policy_analysis.auth.service import AuthenticatedSession, AuthService, PublicUser


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def require_session(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedSession:
    return service.authenticate_session(session_token)


def require_user(session: AuthenticatedSession = Depends(require_session)) -> PublicUser:
    return session.user


def require_csrf_session(
    session: AuthenticatedSession = Depends(require_session),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedSession:
    service.verify_csrf(session, csrf_token)
    return session
