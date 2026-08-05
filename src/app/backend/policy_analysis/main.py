from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from threading import RLock
from urllib.parse import unquote

from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import Engine

from policy_analysis.auth.routes import router as auth_router
from policy_analysis.auth.routes import users_router
from policy_analysis.auth.service import AuthService, UserAdministrationService, UserSyncService
from policy_analysis.collectors.webfetch import WebFetchClient
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.core.errors import install_error_handlers
from policy_analysis.core.settings import AppSettings, load_settings, load_settings_snapshot
from policy_analysis.policies.routes import router as policies_router
from policy_analysis.policies.service import PolicyService
from policy_analysis.settings.routes import router as settings_router
from policy_analysis.sources.bootstrap import bootstrap_default_catalog
from policy_analysis.sources.routes import router as sources_router
from policy_analysis.system.routes import health_router, resolve_build_metadata
from policy_analysis.system.routes import router as system_router
from policy_analysis.tasks.repository import TaskRepository
from policy_analysis.tasks.routes import router as tasks_router
from policy_analysis.tasks.runner import TaskRunner
from policy_analysis.tasks.scheduler import TaskScheduler
from policy_analysis.tasks.worker import TaskWorker

_APPLICATION_ROOT = Path(__file__).resolve().parents[4]
_MIGRATION_LOCK = RLock()


def create_app(
    auth_service: AuthService | None = None,
    frontend_dist: Path | None = None,
    *,
    project_root: Path | None = None,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> FastAPI:
    resolved_project_root = _APPLICATION_ROOT if project_root is None else Path(project_root).resolve()
    resolved_environment = dict(os.environ) if environment is None else dict(environment)
    resolved_config_path = _resolve_factory_path(
        resolved_project_root,
        config_path,
        default=Path("src/config/app.json"),
    )
    resolved_frontend_dist = _resolve_factory_path(
        resolved_project_root,
        frontend_dist,
        default=Path("src/app/frontend/dist"),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine: Engine | None = None
        owns_runtime = app.state.auth_service is None
        try:
            task_worker: TaskWorker | None = None
            task_scheduler: TaskScheduler | None = None
            snapshot = load_settings_snapshot(
                resolved_config_path,
                resolved_project_root,
                resolved_environment,
            )
            app.state.settings = snapshot.settings
            app.state.settings_sources = snapshot.sources
            app.state.settings_environment = resolved_environment
            app.state.build_metadata = resolve_build_metadata(
                resolved_environment,
                resolved_project_root,
            )
            if owns_runtime:
                _upgrade_database(snapshot.settings.database.path)
                default_service, engine = _build_default_auth_service(snapshot.settings)
                app.state.auth_service = default_service
            if isinstance(app.state.auth_service, AuthService):
                service = app.state.auth_service
                app.state.database_sessions = service.sessions
                app.state.user_administration_service = _administration_service_for(service)
                if owns_runtime:
                    service.user_sync.sync_if_changed()
                    bootstrap_default_catalog(service.sessions)
                TaskRepository(service.sessions).recover_interrupted(datetime_now_utc())
                task_worker = _build_task_worker(service.sessions, snapshot.settings)
                task_scheduler = TaskScheduler(service.sessions)
                task_scheduler.set_worker_wakeup(task_worker.submit_next)
                app.state.task_worker = task_worker
                app.state.task_scheduler = task_scheduler
                task_worker.start()
                task_scheduler.start()
            yield
        finally:
            if task_scheduler is not None:
                task_scheduler.shutdown(wait=False)
            if task_worker is not None:
                task_worker.shutdown(wait=True)
            if owns_runtime:
                app.state.settings = None
                app.state.settings_sources = None
                app.state.settings_environment = None
                app.state.build_metadata = None
                app.state.auth_service = None
                app.state.user_administration_service = None
                app.state.database_sessions = None
                app.state.task_worker = None
                app.state.task_scheduler = None
            if owns_runtime and engine is not None:
                engine.dispose()

    app = FastAPI(title="政策分析系统", version="0.1.0", lifespan=lifespan)
    app.state.auth_service = auth_service
    app.state.database_sessions = auth_service.sessions if isinstance(auth_service, AuthService) else None
    app.state.user_administration_service = None
    app.state.settings = None
    app.state.settings_config_path = resolved_config_path
    app.state.settings_environment = None
    app.state.settings_sources = None
    app.state.version_environment = resolved_environment
    app.state.build_metadata = None
    app.state.task_worker = None
    app.state.task_scheduler = None
    app.state.project_root = resolved_project_root
    app.state.frontend_dist = resolved_frontend_dist
    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(settings_router)
    app.include_router(policies_router)
    app.include_router(sources_router)
    app.include_router(tasks_router)
    app.include_router(system_router)
    app.include_router(health_router)
    _install_spa_routes(app, resolved_frontend_dist)
    return app


def datetime_now_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _build_task_worker(sessions, settings: AppSettings) -> TaskWorker:
    if not settings.webfetch.base_url.strip() or not settings.webfetch.api_key.get_secret_value().strip():
        return TaskWorker(
            sessions,
            max_workers=settings.tasks.max_workers,
        )

    def runner_factory():
        webfetch = WebFetchClient(
            settings.webfetch.base_url,
            settings.webfetch.api_key.get_secret_value(),
            timeout_seconds=settings.webfetch.timeout_seconds,
            max_attempts=settings.tasks.retry_attempts,
        )
        policy_service = PolicyService(sessions)
        runner = TaskRunner(
            sessions,
            webfetch,
            policy_service,
            secrets=(settings.webfetch.api_key.get_secret_value(),),
        )
        return runner.run_claimed

    return TaskWorker(
        sessions,
        runner_factory=runner_factory,
        max_workers=settings.tasks.max_workers,
    )


def _resolve_factory_path(project_root: Path, value: Path | None, *, default: Path) -> Path:
    path = default if value is None else Path(value)
    if path.is_absolute():
        return path.resolve()
    resolved = (project_root / path).resolve()
    if not resolved.is_relative_to(project_root):
        raise ValueError("应用工厂相对路径必须位于 project_root 内")
    return resolved


def _install_spa_routes(app: FastAPI, frontend_dist: Path) -> None:
    """Serve a built SPA without weakening API or filesystem boundaries."""

    root = frontend_dist.resolve()
    index = (root / "index.html").resolve()
    if not index.is_file() or not index.is_relative_to(root):
        return

    @app.middleware("http")
    async def serve_spa_fallback(request: Request, call_next):
        response = await call_next(request)
        if request.method != "GET" or response.status_code != 404:
            return response
        if request.scope.get("route") is not None:
            return response

        spa_path = _validated_spa_path(request)
        if spa_path is None:
            return response
        if spa_path in {"api", "health", "docs", "redoc", "openapi.json"}:
            return response
        if spa_path.startswith(("api/", "health/", "docs/", "redoc/", "openapi.json/")):
            return response

        relative_path = Path(spa_path)
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            return response
        if candidate.is_file():
            return _spa_file_response(candidate, response)
        if spa_path.startswith("assets/"):
            return response
        return _spa_file_response(index, response, media_type="text/html")


def _validated_spa_path(request: Request) -> str | None:
    raw_path = request.scope.get("raw_path", b"")
    if not isinstance(raw_path, bytes):
        return None

    decoded = raw_path.decode("latin-1")
    for _ in range(5):
        if _has_unsafe_path_content(decoded):
            return None
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded.removeprefix("/")
        decoded = next_decoded

    if _has_unsafe_path_content(decoded) or unquote(decoded) != decoded:
        return None
    return decoded.removeprefix("/")


def _has_unsafe_path_content(path: str) -> bool:
    return (
        not path.startswith("/")
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(part in {".", ".."} for part in path.split("/"))
    )


def _spa_file_response(path: Path, original: Response, media_type: str | None = None) -> FileResponse:
    request_id = original.headers.get("X-Request-ID")
    headers = {"X-Request-ID": request_id} if request_id else None
    return FileResponse(path, headers=headers, media_type=media_type)


def _build_default_auth_service(settings: AppSettings | None = None) -> tuple[AuthService, Engine]:
    project_root = _APPLICATION_ROOT
    settings = settings or load_settings(project_root / "src/config/app.json", project_root, os.environ)
    engine = build_engine(settings.database.path)
    try:
        sessions = session_factory(engine)
        password_hasher = PasswordHasher()
        user_sync = UserSyncService(settings.auth.password_file, sessions, password_hasher)
        service = AuthService(
            sessions=sessions,
            user_sync=user_sync,
            password_hasher=password_hasher,
            session_hours=settings.auth.session_hours,
            secure_cookie=settings.auth.secure_cookie,
            login_attempts=settings.auth.login_attempts,
            login_window_seconds=settings.auth.login_window_seconds,
            login_max_active_keys=settings.auth.login_max_active_keys,
        )
    except BaseException:
        with suppress(Exception):
            engine.dispose()
        raise
    return service, engine


def _upgrade_database(database_path: Path) -> None:
    """Upgrade the default runtime database without mutating process configuration."""

    try:
        resolved_database_path = Path(database_path).resolve()
        with _MIGRATION_LOCK:
            config = Config()
            config.set_main_option("script_location", str(_APPLICATION_ROOT / "migrations"))
            config.set_main_option("prepend_sys_path", str(_APPLICATION_ROOT))
            config.set_main_option("path_separator", "os")
            config.attributes["database_path"] = resolved_database_path
            command.upgrade(config, "head")
    except Exception:
        raise RuntimeError("数据库迁移失败，应用无法启动。") from None


def _administration_service_for(auth_service: object) -> UserAdministrationService | None:
    if not isinstance(auth_service, AuthService) or not isinstance(auth_service.user_sync, UserSyncService):
        return None
    return UserAdministrationService(
        user_sync=auth_service.user_sync,
        sessions=auth_service.sessions,
        password_hasher=auth_service.password_hasher,
    )


app = create_app()
