import hashlib
import json
import logging
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import policy_analysis.main as main_module
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import User
from policy_analysis.auth.service import AuthService, UserSyncService
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.core.settings import AppSettings
from policy_analysis.main import create_app
from policy_analysis.policies.models import Policy
from policy_analysis.policies.schemas import PolicyWrite
from policy_analysis.policies.service import PolicyService
from policy_analysis.sources.bootstrap import load_seed_manifest
from policy_analysis.sources.models import CollectionRule, PolicyCategory, SeedUrl, Source
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
        "session_factory",
        lambda built_engine: (_ for _ in ()).throw(RuntimeError("session factory failed")),
    )

    with pytest.raises(RuntimeError, match="session factory failed"):
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
    monkeypatch.setattr(main_module, "_upgrade_database", lambda _database_path: None)
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


def test_default_first_startup_migrates_to_head_and_supports_long_chinese_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, database_path = _write_default_runtime_files(tmp_path)
    unrelated_database = tmp_path / "must-not-use.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(unrelated_database))
    unrelated_working_directory = tmp_path / "unrelated-working-directory"
    unrelated_working_directory.mkdir()
    monkeypatch.chdir(unrelated_working_directory)
    app = create_app(
        project_root=tmp_path,
        config_path=config_path.relative_to(tmp_path),
        environment={},
    )

    with _test_client(app) as client:
        policy_id = _insert_searchable_policy(app.state.database_sessions)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/policies", params={"keyword": "中共中央政治局"})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [policy_id]
        with app.state.database_sessions() as database:
            assert database.execute(select_version()).scalar_one() == "0006"

    assert database_path.is_file()
    assert not unrelated_database.exists()
    assert os.environ["POLICY_ANALYSIS_DATABASE__PATH"] == str(unrelated_database)


def test_default_startup_bootstraps_both_default_scenarios(
    tmp_path: Path,
) -> None:
    config_path, _database_path = _write_default_runtime_files(tmp_path)
    app = create_app(
        project_root=tmp_path,
        config_path=config_path.relative_to(tmp_path),
        environment={},
    )

    with _test_client(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/collection-rules")
        assert response.status_code == 200
        assert sorted(item["name"] for item in response.json()) == [
            "中央政治局会议",
            "中央财经委员会会议",
        ]

        with app.state.database_sessions() as database:
            politburo_category = database.scalar(
                select(PolicyCategory).where(PolicyCategory.code == "politburo_meeting")
            )
            finance_category = database.scalar(
                select(PolicyCategory).where(PolicyCategory.code == "finance_council_meeting")
            )
            source = database.scalar(select(Source).where(Source.code == "xinhua"))
            politburo_rule = database.scalar(
                select(CollectionRule).where(CollectionRule.name == "中央政治局会议")
            )
            finance_rule = database.scalar(
                select(CollectionRule).where(CollectionRule.name == "中央财经委员会会议")
            )
            assert politburo_category is not None
            assert finance_category is not None
            assert source is not None
            assert source.name == "新华网"
            assert politburo_rule is not None
            assert politburo_rule.history_years == 5
            assert json.loads(politburo_rule.include_keywords_json) == ["中共中央政治局召开会议"]
            assert finance_rule is not None
            assert finance_rule.history_years == 9
            assert json.loads(finance_rule.include_keywords_json) == ["中央财经委员会"]
            assert database.scalar(
                select(func.count()).select_from(SeedUrl).where(SeedUrl.rule_id == politburo_rule.id)
            ) == len(load_seed_manifest())
            assert (
                database.scalar(
                    select(func.count()).select_from(SeedUrl).where(SeedUrl.rule_id == finance_rule.id)
                )
                > 0
            )

    with _test_client(app), app.state.database_sessions() as database:
        assert database.scalar(select(func.count()).select_from(CollectionRule)) == 2


def test_runtime_migration_serializes_concurrent_upgrades_of_same_database(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(main_module._upgrade_database, [database_path, database_path]))

    assert results == [None, None]
    engine = build_engine(database_path)
    try:
        with engine.connect() as connection:
            assert connection.execute(select_version()).scalar_one() == "0006"
    finally:
        engine.dispose()


def test_runtime_migration_preserves_existing_logger_configuration(tmp_path: Path) -> None:
    database_path = tmp_path / "logging.sqlite3"
    logger = logging.getLogger("policy_analysis.audit.runtime_migration_test")
    original_state = (logger.disabled, logger.level, logger.propagate, list(logger.handlers))
    sentinel_handler = logging.NullHandler()
    logger.disabled = False
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.handlers = [sentinel_handler]
    try:
        main_module._upgrade_database(database_path)

        assert logger.disabled is False
        assert logger.level == logging.ERROR
        assert logger.propagate is False
        assert logger.handlers == [sentinel_handler]
    finally:
        logger.disabled, logger.level, logger.propagate, handlers = original_state
        logger.handlers = handlers


def test_default_startup_upgrades_existing_0002_and_rebuilds_policy_fts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, database_path = _write_default_runtime_files(tmp_path)
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(database_path))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0002")
    monkeypatch.delenv("POLICY_ANALYSIS_DATABASE__PATH")
    engine = build_engine(database_path)
    sessions = session_factory(engine)
    try:
        policy_id = _insert_existing_policy_without_fts(sessions)
    finally:
        engine.dispose()

    app = create_app(
        project_root=tmp_path,
        config_path=config_path.relative_to(tmp_path),
        environment={},
    )
    with _test_client(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/policies", params={"keyword": "存量政策检索"})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [policy_id]
        with app.state.database_sessions() as database:
            assert database.execute(select_version()).scalar_one() == "0006"


def test_default_startup_stops_on_sanitized_migration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _database_path = _write_default_runtime_files(tmp_path)

    def fail_upgrade(*_args, **_kwargs) -> None:
        raise RuntimeError("migration-secret-and-private-path")

    monkeypatch.setattr(command, "upgrade", fail_upgrade)
    monkeypatch.setattr(
        main_module,
        "_build_default_auth_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime-must-not-continue-after-migration-failure")
        ),
    )
    app = create_app(
        project_root=tmp_path,
        config_path=config_path.relative_to(tmp_path),
        environment={},
    )

    with pytest.raises(RuntimeError) as raised, _test_client(app):
        pass

    assert str(raised.value) == "数据库迁移失败，应用无法启动。"
    assert "migration-secret" not in str(raised.value)
    assert "runtime-must-not-continue" not in str(raised.value)


def test_injected_auth_service_does_not_migrate_caller_owned_database(
    auth_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        command,
        "upgrade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("injected database must not be migrated")
        ),
    )

    with _test_client(auth_app) as client:
        assert client.get("/health/live").status_code == 200


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
    monkeypatch.setattr(main_module, "_upgrade_database", lambda _database_path: None)
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
    monkeypatch.setattr(main_module, "_upgrade_database", lambda _database_path: None)
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


def _write_default_runtime_files(project_root: Path) -> tuple[Path, Path]:
    runtime = project_root / "runtime"
    runtime.mkdir()
    password_file = runtime / "password.txt"
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    os.chmod(password_file, 0o600)
    config_path = project_root / "config" / "app.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "database": {"path": "runtime/app.sqlite3"},
                "auth": {"password_file": "runtime/password.txt"},
            }
        ),
        encoding="utf-8",
    )
    return config_path, runtime / "app.sqlite3"


def _insert_searchable_policy(sessions) -> int:
    source_id, category_id, item_id = _insert_catalog_and_task_item(sessions)
    content = "会议研究中共中央政治局工作安排。"
    result = PolicyService(sessions).upsert(
        PolicyWrite(
            source_id=source_id,
            category_id=category_id,
            title="中共中央政治局召开会议",
            canonical_url="https://www.news.cn/politics/startup.html",
            publisher="新华社",
            published_at=datetime(2026, 7, 30, tzinfo=UTC),
            content_text=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            webfetch_artifact_id="startup-artifact",
            crawled_at=datetime(2026, 7, 31, tzinfo=UTC),
        ),
        task_item_id=item_id,
    )
    return result.policy_id


def _insert_catalog_and_task_item(sessions) -> tuple[int, int, int]:
    with sessions.begin() as database:
        source = Source(
            code="startup-xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        category = PolicyCategory(code="startup-meeting", name="会议", is_active=True)
        database.add_all([source, category])
        database.flush()
        rule = CollectionRule(
            source_id=source.id,
            category_id=category.id,
            name="启动测试规则",
            include_keywords_json='["中共中央政治局"]',
            exclude_keywords_json="[]",
            history_years=5,
            discovery_config_json='{"rss_urls":["https://www.news.cn/rss.xml"]}',
            is_active=True,
        )
        database.add(rule)
        database.flush()
        task = CrawlTask(
            rule_id=rule.id,
            trigger_type="manual",
            status="running",
            request_snapshot_json="{}",
        )
        database.add(task)
        database.flush()
        item = CrawlTaskItem(
            task_id=task.id,
            candidate_url="https://www.news.cn/politics/startup.html",
            status="stored",
        )
        database.add(item)
        database.flush()
        return source.id, category.id, item.id


def _insert_existing_policy_without_fts(sessions) -> int:
    with sessions.begin() as database:
        source = Source(
            code="existing-xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        category = PolicyCategory(code="existing-meeting", name="会议", is_active=True)
        database.add_all([source, category])
        database.flush()
        content = "存量政策检索需要迁移后重建索引。"
        policy = Policy(
            source_id=source.id,
            category_id=category.id,
            title="存量政策检索验证",
            canonical_url="https://www.news.cn/politics/existing.html",
            publisher="新华社",
            published_at=datetime(2026, 7, 30, tzinfo=UTC),
            content_text=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            webfetch_artifact_id="existing-artifact",
            first_crawled_at=datetime(2026, 7, 31, tzinfo=UTC),
            last_crawled_at=datetime(2026, 7, 31, tzinfo=UTC),
        )
        database.add(policy)
        database.flush()
        return policy.id


def select_version():
    from sqlalchemy import text

    return text("SELECT version_num FROM alembic_version")
