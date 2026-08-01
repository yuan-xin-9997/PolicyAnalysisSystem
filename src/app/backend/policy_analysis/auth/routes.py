"""Authentication and user-administration API routes."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from policy_analysis.auth.dependencies import (
    get_auth_service,
    require_csrf_session,
    require_user,
)
from policy_analysis.auth.password_file import PasswordFileError, validate_password_entry
from policy_analysis.auth.permissions import PageCode, require_admin, require_admin_csrf
from policy_analysis.auth.service import (
    AuthenticatedSession,
    AuthService,
    PasswordSyncError,
    PublicUser,
    UserAdministrationError,
    UserAdministrationOutcome,
    UserAdministrationService,
)
from policy_analysis.core.errors import APIError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])
audit_logger = logging.getLogger("policy_analysis.audit")


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

    @model_validator(mode="after")
    def validate_password_file_grammar(self) -> CreateUserRequest:
        try:
            validate_password_entry(self.username, self.password, self.role)
        except ValueError:
            raise ValueError("用户名或密码不符合凭据文件语法") from None
        return self


class ChangePasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def validate_password_file_grammar(self) -> ChangePasswordRequest:
        try:
            validate_password_entry("request-validation", self.password, "user")
        except ValueError:
            raise ValueError("密码不符合凭据文件语法") from None
        return self


class ChangeRoleRequest(BaseModel):
    role: Literal["admin", "user"]


class ChangeStatusRequest(BaseModel):
    is_active: bool


class ChangePagesRequest(BaseModel):
    pages: set[PageCode]


def _validate_path_username(value: str) -> str:
    try:
        validate_password_entry(value, "request-validation-password", "user")
    except ValueError:
        raise ValueError("用户名不符合凭据文件语法") from None
    return value


UsernamePath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100),
    AfterValidator(_validate_path_username),
]


def get_user_administration_service(request: Request) -> UserAdministrationService:
    return request.app.state.user_administration_service


@users_router.get("")
def list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    sort_by: Literal["id", "username", "role", "is_active"] = "username",
    sort_order: Literal["asc", "desc"] = "asc",
    _admin: PublicUser = Depends(require_admin),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    allowed_query = {"page", "page_size", "sort_by", "sort_order"}
    if set(request.query_params) - allowed_query:
        raise APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")
    pagination = request.app.state.settings.pagination
    effective_page_size = page_size or pagination.default_page_size
    if effective_page_size > pagination.max_page_size:
        raise APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")
    users, total = _admin_call(
        service.list_users,
        request=request,
        actor=_admin,
        action="list_users",
        target=None,
        page=page,
        page_size=effective_page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {
        "items": [user.to_dict() for user in users],
        "total": total,
        "page": page,
        "page_size": effective_page_size,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }


@users_router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    request: Request,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.create_user,
        payload.username,
        payload.password,
        payload.role,
        {page.value for page in payload.pages},
        request=request,
        actor=_admin,
        action="create_user",
        target=payload.username,
    ).to_dict()


@users_router.patch("/{username}/password")
def change_password(
    username: UsernamePath,
    payload: ChangePasswordRequest,
    request: Request,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.change_password,
        username,
        payload.password,
        request=request,
        actor=_admin,
        action="change_password",
        target=username,
    ).to_dict()


@users_router.patch("/{username}/role")
def change_role(
    username: UsernamePath,
    payload: ChangeRoleRequest,
    request: Request,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.change_role,
        username,
        payload.role,
        request=request,
        actor=_admin,
        action="change_role",
        target=username,
    ).to_dict()


@users_router.patch("/{username}/status")
def change_status(
    username: UsernamePath,
    payload: ChangeStatusRequest,
    request: Request,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.set_active,
        username,
        payload.is_active,
        request=request,
        actor=_admin,
        action="change_status",
        target=username,
    ).to_dict()


@users_router.patch("/{username}/pages")
def change_pages(
    username: UsernamePath,
    payload: ChangePagesRequest,
    request: Request,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: UserAdministrationService = Depends(get_user_administration_service),
) -> dict[str, object]:
    return _admin_call(
        service.set_pages,
        username,
        {page.value for page in payload.pages},
        request=request,
        actor=_admin,
        action="change_pages",
        target=username,
    ).to_dict()


def _admin_call(
    operation,
    *args,
    request: Request,
    actor: PublicUser,
    action: str,
    target: str | None,
    **kwargs,
):
    try:
        return operation(*args, **kwargs)
    except APIError as error:
        _audit_failure(
            request,
            actor,
            action,
            target,
            error.code,
            UserAdministrationOutcome.not_applicable(),
        )
        raise
    except UserAdministrationError as error:
        _audit_failure(
            request,
            actor,
            action,
            target,
            "USER_ADMINISTRATION_FAILED",
            error.outcome,
        )
        raise APIError(
            status_code=503,
            code="USER_ADMINISTRATION_FAILED",
            message="用户管理服务暂时不可用。",
        ) from None
    except (PasswordFileError, PasswordSyncError):
        _audit_failure(
            request,
            actor,
            action,
            target,
            "USER_ADMINISTRATION_FAILED",
            UserAdministrationOutcome.not_applicable(consistency="uncertain"),
        )
        raise APIError(
            status_code=503,
            code="USER_ADMINISTRATION_FAILED",
            message="用户管理服务暂时不可用。",
        ) from None


def _audit_failure(
    request: Request,
    actor: PublicUser,
    action: str,
    target: str | None,
    error_code: str,
    outcome: UserAdministrationOutcome,
) -> None:
    audit_logger.warning(
        "user_management_failed",
        extra={
            "audit": {
                "event": "user_management_failed",
                "actor": actor.username,
                "action": action,
                "target": target,
                "request_id": request.state.request_id,
                "error_code": error_code,
                **outcome.to_audit_dict(),
            }
        },
    )
