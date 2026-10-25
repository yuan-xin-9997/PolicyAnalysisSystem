"""FastAPI dependencies for authenticated requests."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from policy_analysis.auth.service import AuthenticatedSession, AuthService, PublicUser


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def require_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedSession:
    principal = getattr(request.state, "machine_principal", None)
    if principal is not None:
        return AuthenticatedSession(id=0, user=principal.user, csrf_token_hash="")
    return service.authenticate_session(session_token)


def require_user(session: AuthenticatedSession = Depends(require_session)) -> PublicUser:
    return session.user


def require_csrf_session(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedSession:
    if getattr(request.state, "machine_principal", None) is not None:
        return session
    service.verify_csrf(session, csrf_token)
    return session
