"""Closed page-code permissions shared by API authorization and navigation."""

from collections.abc import Callable
from enum import StrEnum

from fastapi import Depends

from policy_analysis.auth.dependencies import require_csrf_session, require_user
from policy_analysis.auth.service import AuthenticatedSession, PublicUser
from policy_analysis.core.errors import APIError


class PageCode(StrEnum):
    POLICIES = "policies"
    TASKS = "tasks"
    PUSH = "push"
    ANALYSIS = "analysis"
    USERS = "users"
    SETTINGS = "settings"


def all_page_codes() -> tuple[str, ...]:
    return tuple(sorted(page.value for page in PageCode))


def can_access(role: str, granted_pages: set[str], required: PageCode) -> bool:
    return role == "admin" or required.value in granted_pages


def require_admin_csrf(
    session: AuthenticatedSession = Depends(require_csrf_session),
) -> PublicUser:
    if session.user.role != "admin":
        raise _permission_denied()
    return session.user


def require_admin(current_user: PublicUser = Depends(require_user)) -> PublicUser:
    if current_user.role != "admin":
        raise _permission_denied()
    return current_user


def require_page(required: PageCode) -> Callable[..., PublicUser]:
    def dependency(current_user: PublicUser = Depends(require_user)) -> PublicUser:
        if not can_access(current_user.role, set(current_user.page_permissions), required):
            raise _permission_denied()
        return current_user

    return dependency


def _permission_denied() -> APIError:
    return APIError(status_code=403, code="PERMISSION_DENIED", message="无权访问此资源。")
