from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable

import httpx
import pytest
from policy_analysis.collectors.base import ExtractedArticle
from policy_analysis.collectors.webfetch import WebFetchClient, WebFetchClientError

ARTICLE_URL = "https://www.news.cn/example/c.html?source=contract-test"
FEED_URL = "https://www.news.cn/example/feed.xml?source=contract-test"
API_KEY = "test-webfetch-key-must-not-leak"


def _article_payload(*, extra: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "req_article_1",
        "adapter": "generic.article",
        "adapter_version": "1",
        "artifact_id": "artifact_1",
        "data": {
            "title": "中共中央政治局召开会议",
            "content": "新华社北京7月30日电 中共中央政治局7月30日召开会议。",
            "author": "",
            "date": "2026-07-30T14:00:00+08:00",
        },
    }
    if extra:
        payload["future_top_level_field"] = {"safe": True}
        data = payload["data"]
        assert isinstance(data, dict)
        data["future_data_field"] = "ignored"
    return payload


def _fetch_payload(*, body: object = "<rss>原始正文</rss>", extra: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "req_fetch_1",
        "success": True,
        "requested_url": FEED_URL,
        "final_url": FEED_URL,
        "status_code": 200,
        "strategy": "httpx",
        "from_cache": False,
        "stale": False,
        "elapsed_ms": 12,
        "content_type": "application/rss+xml; charset=utf-8",
        "body": body,
        "artifact_id": None,
        "fetched_at": "2026-08-01T02:03:04+00:00",
        "attempts": [
            {
                "sequence": 1,
                "strategy": "httpx",
                "status_code": 200,
                "error_code": None,
                "elapsed_ms": 10,
                "upgrade_reason": None,
            }
        ],
    }
    if extra:
        payload["future_response_field"] = [1, 2, 3]
    return payload


def _error_payload(
    *,
    code: object = "RATE_LIMITED",
    message: object = "上游消息不应公开",
    retryable: object = True,
    retry_after_seconds: object = 0.25,
) -> dict[str, object]:
    return {
        "request_id": "req_error_1",
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "retry_after_seconds": retry_after_seconds,
        },
    }


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> WebFetchClient:
    return WebFetchClient(
        "http://webfetch.test",
        API_KEY,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_extract_article_sends_complete_request_and_maps_complete_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/extract"
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert json.loads(request.content) == {
            "url": ARTICLE_URL,
            "adapter": "generic.article",
            "fetch_options": {
                "url": ARTICLE_URL,
                "mode": "auto",
                "save_artifact": True,
            },
        }
        return httpx.Response(200, json=_article_payload())

    with _client(handler) as client:
        result = client.extract_article(ARTICLE_URL)

    assert result == ExtractedArticle(
        request_id="req_article_1",
        artifact_id="artifact_1",
        title="中共中央政治局召开会议",
        content="新华社北京7月30日电 中共中央政治局7月30日召开会议。",
        author="",
        published_hint="2026-07-30T14:00:00+08:00",
    )


def test_fetch_text_sends_fixed_options_and_returns_original_body() -> None:
    original_body = "<?xml version='1.0'?><rss>\n  原样保留  \n</rss>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/fetch"
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert json.loads(request.content) == {
            "url": FEED_URL,
            "mode": "auto",
            "save_artifact": False,
        }
        return httpx.Response(200, json=_fetch_payload(body=original_body))

    with _client(handler) as client:
        assert client.fetch_text(FEED_URL) == original_body


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (200, {"status": "ok"}, True),
        (200, {"status": "starting"}, False),
        (200, {"other": "ok"}, False),
        (503, {"status": "ok"}, False),
    ],
)
def test_ready_maps_only_http_200_status_ok_without_authorization(
    status_code: int,
    payload: dict[str, str],
    expected: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/health/ready"
        assert "Authorization" not in request.headers
        return httpx.Response(status_code, json=payload)

    with _client(handler) as client:
        assert client.ready() is expected


@pytest.mark.parametrize("failure", ["bad_json", "network"])
def test_ready_returns_false_for_invalid_json_or_transport_error(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        if failure == "network":
            raise httpx.ConnectError("transport detail must remain internal", request=request)
        return httpx.Response(200, content=b"not-json")

    with _client(handler) as client:
        assert client.ready() is False


def test_retryable_429_uses_retry_after_and_succeeds_on_third_attempt() -> None:
    request_count = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count < 3:
            return httpx.Response(429, json=_error_payload(retry_after_seconds=0.75))
        return httpx.Response(200, json=_fetch_payload(body="eventual body"))

    with _client(handler, sleep=sleeps.append, max_retry_delay_seconds=0.5) as client:
        assert client.fetch_text(FEED_URL) == "eventual body"

    assert request_count == 3
    assert sleeps == [0.5, 0.5]


def test_non_retryable_422_is_requested_once_and_maps_stable_code() -> None:
    request_count = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            422,
            json=_error_payload(
                code="INVALID_REQUEST",
                retryable=False,
                retry_after_seconds=None,
            ),
        )

    with _client(handler, sleep=sleeps.append) as client, pytest.raises(WebFetchClientError) as caught:
        client.fetch_text(FEED_URL)

    assert request_count == 1
    assert sleeps == []
    assert caught.value.code == "WEBFETCH_INVALID_REQUEST"
    assert caught.value.retryable is False
    assert caught.value.request_id == "req_error_1"
    assert caught.value.retry_after_seconds is None
    assert "上游消息不应公开" not in str(caught.value)


def test_retryable_error_exhaustion_preserves_stable_upstream_error() -> None:
    sleeps: list[float] = []
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(503, json=_error_payload(code="UPSTREAM_TIMEOUT", retry_after_seconds=None))

    with (
        _client(handler, sleep=sleeps.append, retry_base_seconds=0.2) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.extract_article(ARTICLE_URL)

    assert request_count == 3
    assert sleeps == [0.2, 0.4]
    assert caught.value.code == "WEBFETCH_UPSTREAM_TIMEOUT"
    assert caught.value.retryable is True


def test_transport_error_retries_then_recovers_without_real_sleep() -> None:
    request_count = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count < 3:
            raise httpx.ConnectError("internal transport secret", request=request)
        return httpx.Response(200, json=_fetch_payload(body="recovered"))

    with _client(handler, sleep=sleeps.append, retry_base_seconds=0.1) as client:
        assert client.fetch_text(FEED_URL) == "recovered"

    assert request_count == 3
    assert sleeps == [0.1, 0.2]


def test_transport_error_exhaustion_maps_unavailable_without_raw_exception() -> None:
    request_count = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ConnectError("internal transport secret", request=request)

    with _client(handler, sleep=sleeps.append) as client, pytest.raises(WebFetchClientError) as caught:
        client.fetch_text(FEED_URL)

    assert request_count == 3
    assert len(sleeps) == 2
    assert caught.value.code == "WEBFETCH_UNAVAILABLE"
    assert caught.value.retryable is True
    assert caught.value.request_id is None
    assert "internal transport secret" not in str(caught.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("request_id"),
        lambda payload: payload.__setitem__("adapter", 1),
        lambda payload: payload.__setitem__("artifact_id", ""),
        lambda payload: payload.__setitem__("data", {"title": "only-title"}),
        lambda payload: payload["data"].__setitem__("content", None),
    ],
)
def test_extract_invalid_contract_maps_stable_error_without_response_leak(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    payload = _article_payload()
    mutate(payload)
    payload["response_secret"] = "extract-response-secret"

    with (
        _client(lambda _request: httpx.Response(200, json=payload)) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.extract_article(ARTICLE_URL)

    assert caught.value.code == "WEBFETCH_CONTRACT_INVALID"
    assert "extract-response-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("success", False),
        ("requested_url", None),
        ("status_code", "200"),
        ("from_cache", 0),
        ("elapsed_ms", "12.5"),
        ("body", None),
        ("attempts", {}),
        ("elapsed_ms", 12.5),
        ("fetched_at", "not-a-datetime"),
        ("fetched_at", "1700000000"),
        ("fetched_at", "2026-08-01"),
        ("attempts", [{}]),
        (
            "attempts",
            [
                {
                    "sequence": "1",
                    "strategy": "httpx",
                    "status_code": 200,
                    "error_code": None,
                    "elapsed_ms": 10,
                    "upgrade_reason": None,
                }
            ],
        ),
    ],
)
def test_fetch_invalid_contract_maps_stable_error(field: str, value: object) -> None:
    payload = _fetch_payload()
    payload[field] = value

    with (
        _client(lambda _request: httpx.Response(200, json=payload)) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.fetch_text(FEED_URL)

    assert caught.value.code == "WEBFETCH_CONTRACT_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", None),
        ("success", True),
        ("code", 123),
        ("message", None),
        ("retryable", "yes"),
        ("retry_after_seconds", -0.1),
        ("retry_after_seconds", "soon"),
    ],
)
def test_invalid_error_envelope_is_contract_invalid(field: str, value: object) -> None:
    payload = _error_payload()
    if field in {"request_id", "success"}:
        payload[field] = value
    else:
        error = payload["error"]
        assert isinstance(error, dict)
        error[field] = value

    with (
        _client(lambda _request: httpx.Response(503, json=payload), max_attempts=1) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.fetch_text(FEED_URL)

    assert caught.value.code == "WEBFETCH_CONTRACT_INVALID"


def test_http_200_error_envelope_is_contract_invalid() -> None:
    with (
        _client(lambda _request: httpx.Response(200, json=_error_payload())) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.fetch_text(FEED_URL)

    assert caught.value.code == "WEBFETCH_CONTRACT_INVALID"


def test_unsafe_upstream_error_code_maps_request_failed() -> None:
    payload = _error_payload(
        code="invalid-code-with-secret",
        retryable=False,
        retry_after_seconds=None,
    )
    with (
        _client(lambda _request: httpx.Response(400, json=payload)) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.fetch_text(FEED_URL)

    assert caught.value.code == "WEBFETCH_REQUEST_FAILED"
    assert "invalid-code-with-secret" not in str(caught.value)


def test_unknown_response_fields_are_ignored_for_forward_compatibility() -> None:
    responses = iter(
        [
            httpx.Response(200, json=_article_payload(extra=True)),
            httpx.Response(200, json=_fetch_payload(body="future-compatible", extra=True)),
        ]
    )
    with _client(lambda _request: next(responses)) as client:
        assert client.extract_article(ARTICLE_URL).artifact_id == "artifact_1"
        assert client.fetch_text(FEED_URL) == "future-compatible"


class CloseTrackingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.close_count = 0
        self._delegate = httpx.MockTransport(lambda _request: httpx.Response(200, json={"status": "ok"}))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._delegate.handle_request(request)

    def close(self) -> None:
        self.close_count += 1
        self._delegate.close()


def test_context_manager_closes_transport_and_close_is_idempotent() -> None:
    transport = CloseTrackingTransport()
    client = WebFetchClient("https://webfetch.test", API_KEY, transport=transport)

    with client as entered:
        assert entered is client
        assert client.ready() is True

    assert transport.close_count == 1
    client.close()
    assert transport.close_count == 1


@pytest.mark.parametrize(
    ("args", "kwargs", "secret"),
    [
        (("ftp://invalid.example", API_KEY), {}, None),
        (("https://user:password-secret@invalid.example", API_KEY), {}, "password-secret"),
        (("https://invalid.example/?token=query-secret", API_KEY), {}, "query-secret"),
        (("https://invalid.example/#fragment-secret", API_KEY), {}, "fragment-secret"),
        (("https://invalid.example?", API_KEY), {}, None),
        (("https://invalid.example#", API_KEY), {}, None),
        (("https://%zz-host-secret.example", API_KEY), {}, "%zz-host-secret"),
        (("https://invalid.example/prefix-secret", API_KEY), {}, "prefix-secret"),
        (("https://valid.example", "   "), {}, None),
        (("https://valid.example", API_KEY), {"timeout_seconds": 0}, None),
        (("https://valid.example", API_KEY), {"timeout_seconds": math.inf}, None),
        (("https://valid.example", API_KEY), {"max_attempts": 0}, None),
        (("https://valid.example", API_KEY), {"max_attempts": 6}, None),
        (("https://valid.example", API_KEY), {"max_attempts": True}, None),
        (("https://valid.example", API_KEY), {"retry_base_seconds": -1}, None),
        (("https://valid.example", API_KEY), {"retry_base_seconds": math.nan}, None),
        (("https://valid.example", API_KEY), {"max_retry_delay_seconds": -1}, None),
        (("https://valid.example", API_KEY), {"max_retry_delay_seconds": math.inf}, None),
    ],
)
def test_invalid_client_configuration_is_rejected_without_echoing_values(
    args: tuple[str, str],
    kwargs: dict[str, object],
    secret: str | None,
) -> None:
    with pytest.raises(ValueError) as caught:
        WebFetchClient(*args, **kwargs)

    message = str(caught.value)
    assert API_KEY not in message
    if secret is not None:
        assert secret not in message


def test_retry_logs_and_exception_do_not_leak_credentials_urls_or_upstream_messages(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_secret = "upstream-message-secret"
    monkeypatch.setattr(logging.getLogger("policy_analysis.collectors.webfetch"), "disabled", False)
    caplog.set_level(logging.WARNING)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json=_error_payload(message=upstream_secret, retry_after_seconds=0),
        )

    with (
        _client(handler, sleep=lambda _seconds: None) as client,
        pytest.raises(WebFetchClientError) as caught,
    ):
        client.fetch_text(FEED_URL)

    serialized = json.dumps(
        [{"message": record.getMessage(), "record": record.__dict__} for record in caplog.records],
        default=str,
        ensure_ascii=False,
    )
    serialized += str(caught.value)
    for forbidden in (API_KEY, f"Bearer {API_KEY}", "source=contract-test", upstream_secret):
        assert forbidden not in serialized
    assert "WEBFETCH_RATE_LIMITED" in serialized
