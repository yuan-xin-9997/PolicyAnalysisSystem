import warnings
from types import SimpleNamespace

import policy_analysis.main as main_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.main import create_app


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "政策分析系统"


class DisposableEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def test_default_engine_is_disposed_when_startup_construction_fails(monkeypatch) -> None:
    engine = DisposableEngine()
    monkeypatch.setattr(
        main_module,
        "load_settings",
        lambda *args: SimpleNamespace(database=SimpleNamespace(path="ignored.sqlite3")),
    )
    monkeypatch.setattr(main_module, "build_engine", lambda path: engine)
    monkeypatch.setattr(
        main_module,
        "create_schema",
        lambda built_engine: (_ for _ in ()).throw(RuntimeError("schema failed")),
    )

    with pytest.raises(RuntimeError, match="schema failed"):
        main_module._build_default_auth_service()

    assert engine.dispose_calls == 1


def test_default_service_is_rebuilt_and_disposed_for_each_lifespan(monkeypatch) -> None:
    services = [object(), object()]
    engines = [DisposableEngine(), DisposableEngine()]
    build_calls = 0

    def build_default_service():
        nonlocal build_calls
        result = (services[build_calls], engines[build_calls])
        build_calls += 1
        return result

    monkeypatch.setattr(main_module, "_build_default_auth_service", build_default_service)
    app = create_app()

    with _test_client(app):
        assert app.state.auth_service is services[0]
    assert app.state.auth_service is None

    with _test_client(app):
        assert app.state.auth_service is services[1]
    assert app.state.auth_service is None
    assert build_calls == 2
    assert [engine.dispose_calls for engine in engines] == [1, 1]


def test_injected_service_remains_caller_owned_across_lifespans(monkeypatch) -> None:
    injected_service = object()
    monkeypatch.setattr(
        main_module,
        "_build_default_auth_service",
        lambda: (_ for _ in ()).throw(AssertionError("不得构建默认服务")),
    )
    app = create_app(auth_service=injected_service)  # type: ignore[arg-type]

    with _test_client(app):
        assert app.state.auth_service is injected_service
    assert app.state.auth_service is injected_service

    with _test_client(app):
        assert app.state.auth_service is injected_service
    assert app.state.auth_service is injected_service


def _test_client(app: FastAPI) -> TestClient:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient` is deprecated",
            category=DeprecationWarning,
        )
        return TestClient(app)
