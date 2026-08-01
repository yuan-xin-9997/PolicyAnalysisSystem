from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from policy_analysis.core.settings import load_settings_snapshot
from policy_analysis.settings.routes import router as settings_router

ReadyProbe = Callable[..., bool]


def _configure(
    app: FastAPI,
    tmp_path: Path,
    *,
    base_url: str = "https://fetch.example.invalid",
    api_key: str = "status-api-key-must-not-leak",
    timeout_seconds: float = 30,
) -> None:
    config_path = tmp_path / "app.json"
    config_path.write_text(
        json.dumps(
            {
                "webfetch": {
                    "base_url": base_url,
                    "timeout_seconds": timeout_seconds,
                }
            }
        ),
        encoding="utf-8",
    )
    environment = {"POLICY_ANALYSIS_WEBFETCH__API_KEY": api_key} if api_key else {}
    snapshot = load_settings_snapshot(config_path, tmp_path, environment)
    app.state.settings = snapshot.settings
    app.state.settings_sources = snapshot.sources
    app.state.settings_config_path = config_path
    app.state.settings_environment = environment


def _probe_dependency() -> Callable[..., object]:
    route = next(
        route
        for route in settings_router.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/settings/effective"
    )
    dependency = next(
        (
            item.call
            for item in route.dependant.dependencies
            if item.call is not None and item.call.__name__ == "get_webfetch_ready_probe"
        ),
        None,
    )
    assert dependency is not None, "配置端点缺少可覆盖的 WebFetch 就绪探针依赖"
    return dependency


@contextmanager
def _override_probe(app: FastAPI, probe: ReadyProbe) -> Iterator[None]:
    dependency = _probe_dependency()
    app.dependency_overrides[dependency] = lambda: probe
    try:
        yield
    finally:
        app.dependency_overrides.pop(dependency, None)


@pytest.mark.parametrize(
    ("probe_result", "expected_status"),
    [(True, "ready"), (False, "unavailable")],
)
def test_configured_webfetch_reports_injected_probe_result_and_short_timeout(
    admin_client: TestClient,
    auth_app: FastAPI,
    tmp_path: Path,
    probe_result: bool,
    expected_status: str,
) -> None:
    _configure(auth_app, tmp_path, timeout_seconds=45)
    calls: list[dict[str, Any]] = []

    def probe(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return probe_result

    with _override_probe(auth_app, probe):
        response = admin_client.get("/api/v1/settings/effective")

    assert response.status_code == 200
    assert response.json()["webfetch"] == {"status": expected_status, "checked": True}
    assert calls == [
        {
            "base_url": "https://fetch.example.invalid",
            "api_key": "status-api-key-must-not-leak",
            "timeout_seconds": 2.0,
            "max_attempts": 1,
        }
    ]


def test_probe_exception_returns_masked_settings_and_fixed_log_event(
    admin_client: TestClient,
    auth_app: FastAPI,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "raising-api-key-must-not-leak"
    exception_secret = "probe-exception-secret-must-not-leak"
    _configure(auth_app, tmp_path, api_key=api_key)

    def probe(**_kwargs: Any) -> bool:
        raise RuntimeError(exception_secret)

    monkeypatch.setattr(logging.getLogger("policy_analysis.settings.routes"), "disabled", False)
    caplog.set_level(logging.WARNING)
    with _override_probe(auth_app, probe):
        response = admin_client.get("/api/v1/settings/effective")

    assert response.status_code == 200
    assert response.json()["webfetch"] == {"status": "unavailable", "checked": True}
    assert response.json()["values"]["webfetch"]["api_key"] == "********"
    serialized_response = json.dumps(response.json(), ensure_ascii=False)
    serialized_logs = json.dumps(
        [{"message": record.getMessage(), "record": record.__dict__} for record in caplog.records],
        default=str,
        ensure_ascii=False,
    )
    for forbidden in (api_key, exception_secret):
        assert forbidden not in serialized_response
        assert forbidden not in serialized_logs
    assert "webfetch_ready_probe_failed" in serialized_logs


@pytest.mark.parametrize(
    ("base_url", "api_key"),
    [
        ("", "configured-key"),
        ("https://fetch.example.invalid", ""),
    ],
)
def test_incomplete_configuration_is_not_configured_and_never_calls_probe(
    admin_client: TestClient,
    auth_app: FastAPI,
    tmp_path: Path,
    base_url: str,
    api_key: str,
) -> None:
    _configure(auth_app, tmp_path, base_url=base_url, api_key=api_key)

    def fail_if_called(**_kwargs: Any) -> bool:
        raise AssertionError("未配置状态不得调用探针")

    with _override_probe(auth_app, fail_if_called):
        response = admin_client.get("/api/v1/settings/effective")

    assert response.status_code == 200
    assert response.json()["webfetch"] == {"status": "not_configured", "checked": False}


def test_status_probe_does_not_change_admin_masking_or_user_permissions(
    admin_client: TestClient,
    user_client: TestClient,
    auth_app: FastAPI,
    tmp_path: Path,
) -> None:
    api_key = "permission-api-key-must-not-leak"
    _configure(auth_app, tmp_path, api_key=api_key)

    with _override_probe(auth_app, lambda **_kwargs: True):
        admin_response = admin_client.get("/api/v1/settings/effective")
        user_response = user_client.get("/api/v1/settings/effective")

    assert admin_response.status_code == 200
    assert admin_response.json()["values"]["webfetch"]["api_key"] == "********"
    assert admin_response.json()["sources"]["webfetch.api_key"] == "environment"
    assert api_key not in json.dumps(admin_response.json(), ensure_ascii=False)
    assert user_response.status_code == 403


def test_userinfo_base_url_is_not_reflected_in_settings_response(
    admin_client: TestClient,
    auth_app: FastAPI,
    tmp_path: Path,
) -> None:
    base_url = "https://user:userinfo-password@fetch.example.invalid/service"
    _configure(auth_app, tmp_path, base_url=base_url)

    with _override_probe(auth_app, lambda **_kwargs: False):
        response = admin_client.get("/api/v1/settings/effective")

    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 200
    assert "userinfo-password" not in serialized
    assert "user@" not in serialized
    assert response.json()["values"]["webfetch"]["base_url"] == "********"
