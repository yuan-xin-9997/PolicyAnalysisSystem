"""Authentication and user-administration API routes."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from policy_analysis.auth.dependencies import (
    get_auth_service,
    require_csrf_session,
    require_user,
)
from policy_analysis.auth.password_file import PasswordFileError
from policy_analysis.auth.permissions import PageCode, require_admin_csrf
from policy_analysis.auth.service import (
    AuthenticatedSession,
    AuthService,
    PasswordSyncError,
    PublicUser,
    UserAdministrationError,
    UserAdministrationService,
)
from policy_analysis.core.errors import APIError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])


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


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["admin", "user"]
    pages: set[PageCode] = Field(default_factory=set)


class ChangePasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class ChangeRoleRequest(BaseModel):
    role: Literal["admin", "user"]


class ChangeStatusRequest(BaseModel):
    is_active: bool


class ChangePagesRequest(BaseModel):
    pages: set[PageCode]


def get_user_administration_service(request: Request) -> UserAdministrationService:
    return request.app.state.user_administration_service


@users_router.get("")
def list_users(
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return {"items": [user.to_dict() for user in _admin_call(service.list_users)]}


@users_router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.create_user,
        payload.username,
        payload.password,
        payload.role,
        {page.value for page in payload.pages},
    ).to_dict()


@users_router.patch("/{username}/password")
def change_password(
    username: str,
    payload: ChangePasswordRequest,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(service.change_password, username, payload.password).to_dict()


@users_router.patch("/{username}/role")
def change_role(
    username: str,
    payload: ChangeRoleRequest,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(service.change_role, username, payload.role).to_dict()


@users_router.patch("/{username}/status")
def change_status(
    username: str,
    payload: ChangeStatusRequest,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(service.set_active, username, payload.is_active).to_dict()


@users_router.patch("/{username}/pages")
def change_pages(
    username: str,
    payload: ChangePagesRequest,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.set_pages,
        username,
        {page.value for page in payload.pages},
    ).to_dict()


def _admin_call(operation, *args):
    try:
        return operation(*args)
    except (PasswordFileError, PasswordSyncError, UserAdministrationError):
        raise APIError(
            status_code=503,
            code="USER_ADMINISTRATION_FAILED",
            message="用户管理服务暂时不可用。",
        ) from None
