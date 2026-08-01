"""Safe, consistent API error responses."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """An HTTP error whose public fields are safe to return to clients."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def install_error_handlers(app: FastAPI) -> None:
    """Install request-id propagation and the public API error envelope."""

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = secrets.token_urlsafe(16)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, error: APIError) -> JSONResponse:
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
            }
            for item in error.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="请求参数无效。",
            details={"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code, message = {
            404: ("NOT_FOUND", "请求的资源不存在。"),
            405: ("METHOD_NOT_ALLOWED", "请求方法不允许。"),
        }.get(error.status_code, ("HTTP_ERROR", "请求失败。"))
        return _error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        del error
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="服务器内部错误。",
        )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", secrets.token_urlsafe(16))
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details or {},
            }
        },
    )
