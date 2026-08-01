"""Synchronous, contract-validated client for the WebFetch v1 API."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from policy_analysis.collectors.base import ExtractedArticle, WebFetchClientError

logger = logging.getLogger(__name__)

_SAFE_UPSTREAM_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CONTRACT_MESSAGE = "WebFetch 返回数据不符合约定。"
_UNAVAILABLE_MESSAGE = "WebFetch 服务暂时不可用。"
_REQUEST_FAILED_MESSAGE = "WebFetch 请求失败。"


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _ExtractedData(_ResponseModel):
    title: StrictStr
    content: StrictStr
    author: StrictStr
    date: StrictStr


class _ExtractResponse(_ResponseModel):
    request_id: StrictStr
    adapter: Literal["generic.article"]
    adapter_version: StrictStr
    artifact_id: StrictStr = Field(min_length=1)
    data: _ExtractedData


class _AttemptInfo(_ResponseModel):
    sequence: StrictInt
    strategy: StrictStr
    status_code: StrictInt | None = None
    error_code: StrictStr | None = None
    elapsed_ms: StrictInt
    upgrade_reason: StrictStr | None = None


class _FetchResponse(_ResponseModel):
    request_id: StrictStr
    success: Literal[True]
    requested_url: StrictStr
    final_url: StrictStr
    status_code: StrictInt
    strategy: StrictStr
    from_cache: StrictBool
    stale: StrictBool
    elapsed_ms: StrictInt
    content_type: StrictStr | None
    body: StrictStr
    artifact_id: StrictStr | None
    fetched_at: datetime
    attempts: list[_AttemptInfo]

    @field_validator("fetched_at", mode="before")
    @classmethod
    def validate_json_datetime(cls, value: object) -> object:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("invalid fetched_at")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid fetched_at") from None
        return value


class _ErrorDetail(_ResponseModel):
    code: StrictStr
    message: StrictStr
    retryable: StrictBool
    retry_after_seconds: StrictInt | StrictFloat | None = None

    @field_validator("retry_after_seconds")
    @classmethod
    def validate_retry_delay(cls, value: int | float | None) -> int | float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("invalid retry delay")
        return value


class _ErrorEnvelope(_ResponseModel):
    request_id: StrictStr
    success: Literal[False]
    error: _ErrorDetail


class WebFetchClient:
    """Small synchronous WebFetch client with bounded retries and safe errors."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 30,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_base_seconds: float = 0.5,
        max_retry_delay_seconds: float = 5.0,
    ) -> None:
        normalized_base_url = _validated_base_url(base_url)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("WebFetch API Key 必须为非空字符串")
        _validate_positive_finite(timeout_seconds, "WebFetch 超时时间配置无效")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ValueError("WebFetch 最大尝试次数必须在 1 到 5 之间")
        _validate_nonnegative_finite(retry_base_seconds, "WebFetch 重试基础延迟配置无效")
        _validate_nonnegative_finite(max_retry_delay_seconds, "WebFetch 最大重试延迟配置无效")
        if not callable(sleep):
            raise ValueError("WebFetch 等待函数配置无效")

        self._api_key = api_key
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._retry_base_seconds = float(retry_base_seconds)
        self._max_retry_delay_seconds = float(max_retry_delay_seconds)
        try:
            self._client = httpx.Client(
                base_url=normalized_base_url,
                timeout=float(timeout_seconds),
                transport=transport,
            )
        except httpx.InvalidURL:
            raise ValueError("WebFetch 服务地址配置无效") from None
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def extract_article(self, url: str) -> ExtractedArticle:
        response = self._post_with_retries(
            "v1/extract",
            {
                "url": url,
                "adapter": "generic.article",
                "fetch_options": {
                    "url": url,
                    "mode": "auto",
                    "save_artifact": True,
                },
            },
        )
        payload = _validated_model(_ExtractResponse, response)
        return ExtractedArticle(
            request_id=payload.request_id,
            artifact_id=payload.artifact_id,
            title=payload.data.title,
            content=payload.data.content,
            author=payload.data.author,
            published_hint=payload.data.date,
        )

    def fetch_text(self, url: str) -> str:
        response = self._post_with_retries(
            "v1/fetch",
            {"url": url, "mode": "auto", "save_artifact": False},
        )
        return _validated_model(_FetchResponse, response).body

    def ready(self) -> bool:
        try:
            response = self._client.get("health/ready")
            if response.status_code != 200:
                return False
            payload = response.json()
        except (httpx.RequestError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("status") == "ok"

    def _post_with_retries(self, path: str, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    path,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except httpx.RequestError:
                if attempt == self._max_attempts:
                    raise _unavailable_error() from None
                self._log_retry(attempt, None, "WEBFETCH_UNAVAILABLE")
                self._sleep(self._fallback_delay(attempt))
                continue

            if response.status_code == 200:
                return response
            if 200 <= response.status_code < 300:
                raise _contract_error()

            error = _validated_upstream_error(response)
            if not error.retryable or attempt == self._max_attempts:
                raise error
            self._log_retry(attempt, response.status_code, error.code)
            self._sleep(self._retry_delay(error, attempt))

        raise _unavailable_error()

    def _fallback_delay(self, attempt: int) -> float:
        return min(self._retry_base_seconds * (2 ** (attempt - 1)), self._max_retry_delay_seconds)

    def _retry_delay(self, error: WebFetchClientError, attempt: int) -> float:
        if error.retry_after_seconds is None:
            return self._fallback_delay(attempt)
        return min(error.retry_after_seconds, self._max_retry_delay_seconds)

    @staticmethod
    def _log_retry(attempt: int, status: int | None, code: str) -> None:
        logger.warning(
            "webfetch_retry",
            extra={"attempt": attempt, "http_status": status, "error_code": code},
        )


def _validated_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("WebFetch 服务地址配置无效")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("WebFetch 服务地址配置无效") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or "?" in value
        or "#" in value
        or "%" in parsed.hostname
        or port is None
        and parsed.netloc.endswith(":")
    ):
        raise ValueError("WebFetch 服务地址配置无效")
    return value.rstrip("/") + "/"


def _validate_positive_finite(value: object, message: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(message)


def _validate_nonnegative_finite(value: object, message: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ValueError(message)


def _validated_model[ModelT: BaseModel](model: type[ModelT], response: httpx.Response) -> ModelT:
    try:
        payload = response.json()
        return model.model_validate(payload)
    except (ValueError, ValidationError):
        raise _contract_error() from None


def _validated_upstream_error(response: httpx.Response) -> WebFetchClientError:
    envelope = _validated_model(_ErrorEnvelope, response)
    upstream_code = envelope.error.code
    code = (
        f"WEBFETCH_{upstream_code}"
        if _SAFE_UPSTREAM_CODE.fullmatch(upstream_code)
        else "WEBFETCH_REQUEST_FAILED"
    )
    delay = envelope.error.retry_after_seconds
    return WebFetchClientError(
        code=code,
        message=_REQUEST_FAILED_MESSAGE,
        retryable=envelope.error.retryable,
        request_id=envelope.request_id,
        retry_after_seconds=None if delay is None else float(delay),
    )


def _contract_error() -> WebFetchClientError:
    return WebFetchClientError(
        code="WEBFETCH_CONTRACT_INVALID",
        message=_CONTRACT_MESSAGE,
        retryable=False,
    )


def _unavailable_error() -> WebFetchClientError:
    return WebFetchClientError(
        code="WEBFETCH_UNAVAILABLE",
        message=_UNAVAILABLE_MESSAGE,
        retryable=True,
    )


__all__ = ["WebFetchClient", "WebFetchClientError"]
