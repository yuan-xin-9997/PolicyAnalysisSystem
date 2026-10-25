from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from policy_analysis.ai.models import AuditEvent, Credential, OAuthAccess
from policy_analysis.ai.service import (
    SCOPE_DESCRIPTIONS,
    SCOPES,
    Principal,
    audit,
    authenticate,
    available_scopes,
    create_credential,
    credential_public,
    denied,
    digest,
    now,
    public_base,
    resource_url,
)
from policy_analysis.auth.dependencies import require_csrf_session, require_session
from policy_analysis.auth.permissions import require_admin
from policy_analysis.auth.service import AuthenticatedSession, PublicUser

router = APIRouter(tags=["automation"])


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPES))
    expires_at: datetime


class CredentialRead(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class CredentialIssued(CredentialRead):
    secret: str = Field(description="仅本次返回；服务端仅存 SHA-256 摘要，请保存到受保护的凭据存储。")


class ManagedCredential(CredentialRead):
    status: str
    effective_scopes: list[str]


class CredentialPage(BaseModel):
    items: list[ManagedCredential]
    total: int
    page: int
    page_size: int


class ScopeOption(BaseModel):
    code: str
    label: str
    description: str
    read_only: bool


class ToolInfo(BaseModel):
    name: str
    description: str | None
    annotations: dict[str, Any]


class ClientInfo(BaseModel):
    name: str
    status: str
    description: str


class AccessInfo(BaseModel):
    api_url: str
    openapi_url: str
    docs_url: str
    mcp_url: str
    token_url: str
    issuer: str
    scopes: list[ScopeOption]
    tools: list[ToolInfo]
    clients: list[ClientInfo]


@router.get("/api/v1/ai-access", response_model=AccessInfo)
async def access_info(
    request: Request, response: Response, session: AuthenticatedSession = Depends(require_session)
):
    response.headers["Cache-Control"] = "no-store"
    base = public_base(request.app)
    tools = await request.app.state.mcp_server.list_tools()
    return {
        "api_url": base + "/api/v1",
        "openapi_url": base + "/openapi.json",
        "docs_url": base + "/docs",
        "mcp_url": resource_url(request.app),
        "token_url": base + "/oauth/token",
        "issuer": base,
        "scopes": [
            {
                "code": scope,
                "label": SCOPE_DESCRIPTIONS[scope][0],
                "description": SCOPE_DESCRIPTIONS[scope][1],
                "read_only": scope.endswith(":read"),
            }
            for scope in sorted(available_scopes(session.user))
        ],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "annotations": tool.annotations.model_dump() if tool.annotations else {},
            }
            for tool in tools
        ],
        "clients": [
            {
                "name": "官方 Python MCP SDK",
                "status": "已实测",
                "description": "ClientCredentialsOAuthProvider 自动发现、获取及续取短期令牌。",
            },
            {
                "name": "支持 Bearer 头的 Streamable HTTP 客户端",
                "status": "协议适配，未逐一实测",
                "description": "由客户端安全获取和续取 OAuth 短期令牌；不能直接使用普通 API Key。",
            },
            {
                "name": "要求交互式 OAuth/PKCE 的客户端",
                "status": "尚不支持",
                "description": (
                    "当前不提供 authorization_code/PKCE 或动态客户端注册，"
                    "不承诺全部 ChatGPT/Claude/Cursor 连接方式。"
                ),
            },
        ],
    }


@router.get("/api/v1/ai-access/credentials", response_model=CredentialPage)
def credential_page(
    request: Request,
    response: Response,
    session: AuthenticatedSession = Depends(require_session),
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(10, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    with request.app.state.database_sessions() as db:
        total = db.scalar(
            select(func.count()).select_from(Credential).where(Credential.user_id == session.user.id)
        )
        rows = db.scalars(
            select(Credential)
            .where(Credential.user_id == session.user.id)
            .order_by(Credential.created_at.desc(), Credential.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        allowed = available_scopes(session.user)
        items = [
            {
                **credential_public(row),
                "status": "revoked"
                if row.revoked_at
                else ("expired" if row.expires_at <= now() else "active"),
                "effective_scopes": sorted(set(json.loads(row.scopes_json)) & allowed),
            }
            for row in rows
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/api/v1/credentials", status_code=201, response_model=CredentialIssued)
def issue_credential(
    body: CredentialCreate,
    request: Request,
    response: Response,
    session: AuthenticatedSession = Depends(require_csrf_session),
):
    response.headers["Cache-Control"] = "no-store"
    return create_credential(request.app, session.user, body.name, body.scopes, body.expires_at)


@router.get("/api/v1/credentials", response_model=list[CredentialRead])
def list_credentials(
    request: Request,
    response: Response,
    session: AuthenticatedSession = Depends(require_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    with request.app.state.database_sessions() as db:
        rows = db.scalars(
            select(Credential)
            .where(Credential.user_id == session.user.id)
            .order_by(Credential.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return [credential_public(row) for row in rows]


@router.delete("/api/v1/credentials/{credential_id}", status_code=204)
def revoke_credential(
    credential_id: str, request: Request, session: AuthenticatedSession = Depends(require_csrf_session)
):
    with request.app.state.database_sessions.begin() as db:
        row = db.get(Credential, credential_id)
        if row is None or row.user_id != session.user.id:
            raise denied(404, "CREDENTIAL_NOT_FOUND")
        row.revoked_at = now()
    audit(
        request.app,
        Principal(credential_id, session.user, frozenset()),
        "REVOKE",
        "/api/v1/credentials/" + credential_id,
        204,
    )
    return Response(status_code=204)


@router.get("/api/v1/audit-events")
def list_audit(
    request: Request,
    _admin: PublicUser = Depends(require_admin),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    with request.app.state.database_sessions() as db:
        events = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).offset(offset).limit(limit))
        return [
            {
                k: getattr(row, k)
                for k in (
                    "id",
                    "credential_id",
                    "user_id",
                    "operation",
                    "target",
                    "status_code",
                    "created_at",
                )
            }
            for row in events
        ]


@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
def protected_metadata(request: Request):
    return {
        "resource": resource_url(request.app),
        "authorization_servers": [public_base(request.app)],
        "scopes_supported": sorted(SCOPES),
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
def authorization_metadata(request: Request):
    base = public_base(request.app)
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "response_types_supported": [],
        "scopes_supported": sorted(SCOPES),
    }


@router.get("/oauth/authorize", include_in_schema=False)
def unsupported_browser_authorization():
    return JSONResponse(
        {
            "error": "unsupported_response_type",
            "error_description": "Use the MCP client_credentials extension with pre-provisioned credentials.",
        },
        status_code=400,
    )


@router.post("/oauth/token", include_in_schema=False)
async def oauth_token(request: Request):
    # OAuth client_credentials is the explicit MCP machine-to-machine extension, not browser OAuth.
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 8192:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
        if any(len(values) != 1 for values in fields.values()):
            raise ValueError("duplicate field")
        form = {key: values[0] for key, values in fields.items()}
    except (ValueError, UnicodeError):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    try:
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        client_id, client_secret = str(form.get("client_id", "")), str(form.get("client_secret", ""))
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Basic "):
            try:
                encoded = base64.b64decode(authorization[6:], validate=True).decode()
                client_id, client_secret = [unquote(value) for value in encoded.split(":", 1)]
            except (ValueError, UnicodeError):
                raise denied() from None
        principal = authenticate(request.app, client_secret)
        if principal.credential_id != client_id:
            raise denied()
        if form.get("resource") != resource_url(request.app):
            return JSONResponse({"error": "invalid_target"}, status_code=400)
        scopes = set(str(form["scope"]).split()) if "scope" in form else set(principal.scopes)
        if not scopes or not scopes <= SCOPES.keys():
            return JSONResponse({"error": "invalid_scope"}, status_code=400)
        scopes &= principal.scopes
        if not scopes:
            return JSONResponse({"error": "invalid_scope"}, status_code=400)
        token = secrets.token_urlsafe(48)
        with request.app.state.database_sessions.begin() as db:
            credential = db.get(Credential, principal.credential_id)
            if credential.secret_hash != digest(client_secret):
                raise denied()
            expiry = min(credential.expires_at, now() + timedelta(minutes=15))
            db.add(
                OAuthAccess(
                    token_hash=digest(token),
                    credential_id=credential.id,
                    resource=resource_url(request.app),
                    scopes_json=json.dumps(sorted(scopes)),
                    expires_at=expiry,
                )
            )
        audit(request.app, principal, "TOKEN", "/oauth/token", 200)
        return JSONResponse(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": max(0, int((expiry - now()).total_seconds())),
                "scope": " ".join(sorted(scopes)),
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except Exception as error:
        from policy_analysis.core.errors import APIError

        if not isinstance(error, APIError):
            raise
        return JSONResponse(
            {"error": "invalid_client"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
