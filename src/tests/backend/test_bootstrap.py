import warnings
from pathlib import Path
from types import SimpleNamespace

import policy_analysis.main as main_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import User
from policy_analysis.auth.service import AuthService, UserSyncService
from policy_analysis.core.settings import AppSettings
from policy_analysis.main import create_app
from sqlalchemy import select


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "政策分析系统"


def test_create_app_explicit_empty_environment_and_default_frontend_use_injected_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICY_ANALYSIS_SERVER__PORT", "39999")
    config_path = tmp_path / "config" / "app.json"
    config_path.parent.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    password_file = tmp_path / "src" / "data" / "password.txt"
    password_file.parent.mkdir(parents=True)
    password_file.write_text("", encoding="utf-8")
    password_file.chmod(0o600)
    frontend_dist = tmp_path / "src" / "app" / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<html>injected root</html>", encoding="utf-8")

    app = create_app(
        project_root=tmp_path,
        config_path=Path("config/app.json"),
        environment={},
    )

    assert app.state.project_root == tmp_path.resolve()
    assert app.state.settings_config_path == config_path.resolve()
    assert app.state.frontend_dist == frontend_dist.resolve()
    assert app.state.version_environment == {}
    with _test_client(app) as client:
        assert app.state.settings.server.port == 30080
        assert app.state.settings_environment == {}
        response = client.get("/login")
        assert response.status_code == 200
        assert response.text == "<html>injected root</html>"


@pytest.mark.parametrize("path_argument", ["config_path", "frontend_dist"])
def test_create_app_rejects_relative_factory_paths_that_escape_injected_root(
    tmp_path: Path,
    path_argument: str,
) -> None:
    with pytest.raises(ValueError, match="相对路径必须位于 project_root"):
        create_app(
            project_root=tmp_path,
            environment={},
            **{path_argument: Path("../outside")},
        )


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

    def build_default_service(_settings=None):
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
        lambda _settings=None: (_ for _ in ()).throw(AssertionError("不得构建默认服务")),
    )
    app = create_app(auth_service=injected_service)  # type: ignore[arg-type]

    with _test_client(app):
        assert app.state.auth_service is injected_service
    assert app.state.auth_service is injected_service

    with _test_client(app):
        assert app.state.auth_service is injected_service
    assert app.state.auth_service is injected_service


def test_default_service_builder_constructs_complete_runtime_with_temporary_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = AppSettings.model_validate(
        {
            "database": {"path": tmp_path / "app.sqlite3"},
            "auth": {"password_file": tmp_path / "password.txt"},
        }
    )
    monkeypatch.setattr(main_module, "load_settings", lambda *_args: settings)

    service, engine = main_module._build_default_auth_service()

    try:
        assert isinstance(service, AuthService)
        assert service.sessions.kw["bind"] is engine
        assert service.user_sync._password_file == tmp_path / "password.txt"
    finally:
        engine.dispose()


def test_default_lifespan_synchronizes_password_file_before_serving(
    password_file: Path,
    database_sessions,
    password_hasher,
    mutable_clock,
    monkeypatch,
) -> None:
    password_file.write_text("startup-admin:startup-password:admin\n", encoding="utf-8")
    service = AuthService(
        sessions=database_sessions,
        user_sync=UserSyncService(password_file, database_sessions, password_hasher),
        password_hasher=password_hasher,
        session_hours=12,
        secure_cookie=False,
        login_attempts=3,
        login_window_seconds=60,
        login_max_active_keys=100,
        now=mutable_clock.now,
        monotonic=mutable_clock.monotonic,
    )
    engine = DisposableEngine()
    monkeypatch.setattr(main_module, "_build_default_auth_service", lambda _settings=None: (service, engine))
    monkeypatch.setattr(main_module, "load_settings", lambda *_args: AppSettings())
    app = create_app()

    with _test_client(app), database_sessions() as database:
        assert database.scalar(select(User).where(User.username == "startup-admin")) is not None

    assert engine.dispose_calls == 1


@pytest.mark.parametrize("failure_stage", ["administration", "settings"])
def test_default_lifespan_cleans_half_initialized_runtime_on_setup_failure(
    password_file: Path,
    database_sessions,
    password_hasher,
    mutable_clock,
    monkeypatch,
    failure_stage: str,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    service = AuthService(
        sessions=database_sessions,
        user_sync=UserSyncService(password_file, database_sessions, password_hasher),
        password_hasher=password_hasher,
        session_hours=12,
        secure_cookie=False,
        login_attempts=3,
        login_window_seconds=60,
        login_max_active_keys=100,
        now=mutable_clock.now,
        monotonic=mutable_clock.monotonic,
    )
    engine = DisposableEngine()
    monkeypatch.setattr(main_module, "_build_default_auth_service", lambda _settings=None: (service, engine))
    if failure_stage == "administration":
        monkeypatch.setattr(
            main_module,
            "_administration_service_for",
            lambda _service: (_ for _ in ()).throw(RuntimeError("administration setup failed")),
        )
    else:
        monkeypatch.setattr(
            main_module,
            "load_settings_snapshot",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("settings setup failed")),
        )
    app = create_app()

    with pytest.raises(RuntimeError, match="setup failed"), _test_client(app):
        pass

    expected_dispose_calls = 0 if failure_stage == "settings" else 1
    assert engine.dispose_calls == expected_dispose_calls
    assert app.state.auth_service is None
    assert app.state.user_administration_service is None
    assert app.state.database_sessions is None


def _test_client(app: FastAPI) -> TestClient:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient` is deprecated",
            category=DeprecationWarning,
        )
        return TestClient(app)
