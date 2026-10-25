from __future__ import annotations

import json
import re
import secrets
import time
from collections import OrderedDict
from contextvars import ContextVar
from threading import Lock

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from policy_analysis.ai.models import IdempotentRequest
from policy_analysis.ai.service import audit, authenticate, denied, digest, now, required_scope, resource_url
from policy_analysis.core.errors import APIError

mcp_token: ContextVar[str] = ContextVar("mcp_token")


class AutomationMiddleware:
    """ASGI boundary: no cookies can rescue an invalid Authorization header."""

    def __init__(self, app, owner):
        self.app, self.owner = app, owner
        self.windows = OrderedDict()
        self.lock = Lock()

    def limit(self, key, maximum):
        minute = int(time.monotonic() // 60)
        with self.lock:
            old_minute, count = self.windows.pop(key, (minute, 0))
            count = count + 1 if old_minute == minute else 1
            self.windows[key] = (minute, count)
            while len(self.windows) > 4096:
                self.windows.popitem(last=False)
            if count > maximum:
                raise denied(429, "RATE_LIMITED")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = request.url.path, request.method
        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        authorization = request.headers.get("authorization")
        if not is_mcp and not (path.startswith("/api/") and authorization is not None):
            if path == "/oauth/token":
                try:
                    self.limit(("oauth", scope.get("client", ("unknown",))[0]), 30)
                except APIError as error:
                    return await self.error(error, scope, receive, send)
            return await self.app(scope, receive, send)
        principal = None
        context = None
        record_key = None
        writing = method not in {"GET", "HEAD", "OPTIONS"} and not is_mcp
        try:
            self.limit(("peer", scope.get("client", ("unknown",))[0]), 600)
            if not authorization or not authorization.startswith("Bearer "):
                raise denied()
            token = authorization[7:]
            principal = authenticate(self.owner, token, oauth_only=is_mcp)
            self.limit(
                ("credential", principal.credential_id), self.owner.state.settings.ai.requests_per_minute
            )
            if is_mcp:
                if not principal.scopes:
                    raise denied(403, "INSUFFICIENT_SCOPE")
                context = mcp_token.set(token)
                return await self.dispatch_mcp(scope, receive, send, principal)
            required = required_scope(path, method)
            if required is None or required not in principal.scopes:
                raise denied(403, "INSUFFICIENT_SCOPE")
            if writing and required == "rules:write" and request.headers.get("x-confirm-operation") != "true":
                raise denied(422, "OPERATION_CONFIRMATION_REQUIRED")
            scope.setdefault("state", {})["machine_principal"] = principal
            body = b""
            if writing:
                key = request.headers.get("idempotency-key", "")
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
                    raise denied(422, "IDEMPOTENCY_KEY_REQUIRED")
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body += message.get("body", b"")
                    if len(body) > self.owner.state.settings.ai.max_body_bytes:
                        raise denied(413, "BODY_TOO_LARGE")
                    if not message.get("more_body", False):
                        break
                fingerprint = digest((method + path + "?").encode() + scope["query_string"] + b"\n" + body)
                record_key = (principal.credential_id, digest(key))
                with self.owner.state.database_sessions() as db:
                    record = db.get(IdempotentRequest, record_key)
                    if record:
                        if record.fingerprint != fingerprint:
                            raise denied(409, "IDEMPOTENCY_CONFLICT")
                        if record.status_code is None:
                            raise denied(409, "IDEMPOTENCY_IN_PROGRESS")
                        audit(self.owner, principal, method, path, record.status_code)
                        response = JSONResponse(
                            json.loads(record.response_body),
                            status_code=record.status_code,
                            headers={"Idempotency-Replayed": "true"},
                        )
                        return await response(scope, receive, send)
                try:
                    with self.owner.state.database_sessions.begin() as db:
                        db.add(
                            IdempotentRequest(
                                credential_id=record_key[0],
                                key_hash=record_key[1],
                                fingerprint=fingerprint,
                                created_at=now(),
                            )
                        )
                except IntegrityError:
                    raise denied(409, "IDEMPOTENCY_IN_PROGRESS") from None

                delivered = False

                async def replay_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()

                messages = []

                async def capture(message):
                    messages.append(message)

                try:
                    await self.app(scope, replay_receive, capture)
                except Exception:
                    audit(self.owner, principal, method, path, 500)
                    raise
                status_code = next(m["status"] for m in messages if m["type"] == "http.response.start")
                response_body = b"".join(m.get("body", b"") for m in messages)
                with self.owner.state.database_sessions.begin() as db:
                    record = db.get(IdempotentRequest, record_key)
                    record.status_code = status_code
                    record.response_body = response_body.decode() or "null"
                audit(self.owner, principal, method, path, status_code)
                for message in messages:
                    await send(message)
                return
            return await self.app(scope, receive, send)
        except APIError as error:
            if writing or (is_mcp and method == "POST"):
                audit(self.owner, principal, method, path, error.status_code)
            return await self.error(error, scope, receive, send, is_mcp)
        finally:
            if context is not None:
                mcp_token.reset(context)

    async def dispatch_mcp(self, scope, receive, send, principal):
        if scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > self.owner.state.settings.ai.max_body_bytes:
                raise denied(413, "BODY_TOO_LARGE")
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            data = json.loads(body)
            tool = data.get("params", {}).get("name", "") if data.get("method") == "tools/call" else ""
        except (ValueError, AttributeError):
            tool = ""
        if not isinstance(tool, str) or tool not in {
            "create_collection_rule",
            "update_collection_rule",
            "create_collection_task",
            "create_analysis_task",
            "create_comparison_task",
        }:
            return await self.app(scope, replay_receive, send)
        messages = []

        async def capture(message):
            messages.append(message)

        await self.app(scope, replay_receive, capture)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        try:
            response = json.loads(b"".join(m.get("body", b"") for m in messages))
            if response.get("error") or response.get("result", {}).get("isError"):
                status = 400
        except (ValueError, AttributeError):
            status = 500
        audit(self.owner, principal, "MCP_WRITE", tool, status)
        for message in messages:
            await send(message)

    async def error(self, error, scope, receive, send, is_mcp=False):
        headers = {}
        if error.status_code == 429:
            headers["Retry-After"] = "60"
        if error.status_code == 401:
            metadata = resource_url(self.owner).removesuffix("/mcp/")
            headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{metadata}/.well-known/oauth-protected-resource/mcp"'
                if is_mcp
                else "Bearer"
            )
        request_id = scope.get("state", {}).get("request_id", secrets.token_urlsafe(16))
        headers["X-Request-ID"] = request_id
        response = JSONResponse(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                    "request_id": request_id,
                }
            },
            status_code=error.status_code,
            headers=headers,
        )
        await response(scope, receive, send)
