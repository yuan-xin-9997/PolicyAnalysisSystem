from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from policy_analysis.ai.models import AuditEvent, Credential, OAuthAccess
from policy_analysis.auth.models import User
from policy_analysis.auth.service import PublicUser, _public_user
from policy_analysis.core.errors import APIError

SCOPES = {
    "policies:read": "policies",
    "tasks:read": "tasks",
    "tasks:write": "tasks",
    "rules:read": "tasks",
    "rules:write": "tasks",
    "analysis:read": "analysis",
    "analysis:write": "analysis",
}
ADMIN_SCOPES = {"tasks:write", "rules:write"}


def now():
    return datetime.now(UTC)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def denied(status=401, code="INVALID_CREDENTIAL"):
    return APIError(status_code=status, code=code, message="凭据无效、已过期或权限不足。")


@dataclass(frozen=True)
class Principal:
    credential_id: str
    user: PublicUser
    scopes: frozenset[str]


def authenticate(app, token: str, *, oauth_only=False) -> Principal:
    if not token or len(token) > 512:
        raise denied()
    app.state.auth_service.user_sync.sync_if_changed()
    with app.state.database_sessions() as db:
        access = db.get(OAuthAccess, digest(token))
        if access is not None:
            if access.expires_at <= now() or access.resource != resource_url(app):
                raise denied()
            credential = db.get(Credential, access.credential_id)
        else:
            if oauth_only:
                raise denied()
            credential = db.scalar(select(Credential).where(Credential.secret_hash == digest(token)))
        if credential is None or credential.revoked_at or credential.expires_at <= now():
            raise denied()
        user = db.scalar(
            select(User)
            .options(joinedload(User.page_permissions))
            .where(
                User.id == credential.user_id,
            )
        )
        if user is None or not user.is_active:
            raise denied()
        scopes = set(json.loads(credential.scopes_json))
        if access:
            scopes &= set(json.loads(access.scopes_json))
        public = _public_user(user)
        scopes = {
            scope
            for scope in scopes
            if SCOPES[scope] in public.page_permissions
            and (scope not in ADMIN_SCOPES or public.role == "admin")
        }
        return Principal(credential.id, public, frozenset(scopes))


def public_base(app) -> str:
    settings = app.state.settings
    configured = settings.ai.public_base_url.rstrip("/")
    if configured:
        return configured
    # Local development only; remote deployments explicitly configure their existing URL.
    return f"http://{settings.server.host}:{settings.server.port}"


def resource_url(app) -> str:
    return public_base(app) + "/mcp/"


def create_credential(app, user, name, scopes, expires_at):
    if not scopes or not set(scopes) <= SCOPES.keys():
        raise denied(422, "INVALID_SCOPE")
    if expires_at.tzinfo is None or not now() < expires_at <= now() + timedelta(days=365):
        raise denied(422, "INVALID_EXPIRY")
    if any(
        SCOPES[s] not in user.page_permissions or (s in ADMIN_SCOPES and user.role != "admin") for s in scopes
    ):
        raise denied(403, "INSUFFICIENT_SCOPE")
    secret = "pa_" + secrets.token_urlsafe(32)
    record = Credential(
        id=secrets.token_hex(16),
        user_id=user.id,
        name=name,
        secret_hash=digest(secret),
        scopes_json=json.dumps(sorted(set(scopes))),
        created_at=now(),
        expires_at=expires_at,
        revoked_at=None,
    )
    with app.state.database_sessions.begin() as db:
        db.add(record)
    audit(app, Principal(record.id, user, frozenset(scopes)), "CREATE", "/api/v1/credentials", 201)
    return {**credential_public(record), "secret": secret}


def credential_public(record):
    return {
        "id": record.id,
        "name": record.name,
        "scopes": json.loads(record.scopes_json),
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
    }


def audit(app, principal, operation, target, status_code):
    with app.state.database_sessions.begin() as db:
        db.add(
            AuditEvent(
                credential_id=principal.credential_id if principal else None,
                user_id=principal.user.id if principal else None,
                operation=operation,
                target=target[:512],
                status_code=status_code,
                created_at=now(),
            )
        )


def required_scope(path, method):
    read = method in {"GET", "HEAD"}
    suffix = "read" if read else "write"
    if path == "/api/v1/policies" or path.startswith("/api/v1/policies/"):
        return "policies:read" if read else None
    if path == "/api/v1/tasks" or path.startswith("/api/v1/tasks/"):
        return f"tasks:{suffix}"
    if path == "/api/v1/collection-rules" or path.startswith("/api/v1/collection-rules/"):
        return f"rules:{suffix}"
    if path in {"/api/v1/sources", "/api/v1/policy-categories"} and read:
        return "rules:read"
    if path.startswith("/api/v1/analysis/"):
        return f"analysis:{suffix}"
    return None
