from __future__ import annotations

import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import unquote

from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from sqlalchemy import Engine

from policy_analysis.auth.routes import router as auth_router
from policy_analysis.auth.routes import users_router
from policy_analysis.auth.service import AuthService, UserAdministrationService, UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from policy_analysis.core.errors import install_error_handlers
from policy_analysis.core.settings import AppSettings, load_settings, load_settings_snapshot
from policy_analysis.settings.routes import router as settings_router
from policy_analysis.system.routes import health_router, resolve_build_metadata
from policy_analysis.system.routes import router as system_router


def create_app(
    auth_service: AuthService | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    project_root = Path(__file__).resolve().parents[4]
    environment = dict(os.environ)
    config_path = project_root / "src/config/app.json"
    resolved_frontend_dist = frontend_dist or project_root / "src/app/frontend/dist"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine: Engine | None = None
        owns_runtime = app.state.auth_service is None
        try:
            snapshot = load_settings_snapshot(config_path, project_root, environment)
            app.state.settings = snapshot.settings
            app.state.settings_sources = snapshot.sources
            app.state.settings_environment = environment
            app.state.build_metadata = resolve_build_metadata(environment, project_root)
            if owns_runtime:
                default_service, engine = _build_default_auth_service(snapshot.settings)
                app.state.auth_service = default_service
            if isinstance(app.state.auth_service, AuthService):
                service = app.state.auth_service
                app.state.database_sessions = service.sessions
                app.state.user_administration_service = _administration_service_for(service)
                if owns_runtime:
                    service.user_sync.sync_if_changed()
            yield
        finally:
            if owns_runtime:
                app.state.settings = None
                app.state.settings_sources = None
                app.state.settings_environment = None
                app.state.build_metadata = None
                app.state.auth_service = None
                app.state.user_administration_service = None
                app.state.database_sessions = None
            if owns_runtime and engine is not None:
                engine.dispose()

    app = FastAPI(title="政策分析系统", version="0.1.0", lifespan=lifespan)
    app.state.auth_service = auth_service
    app.state.database_sessions = auth_service.sessions if isinstance(auth_service, AuthService) else None
    app.state.user_administration_service = None
    app.state.settings = None
    app.state.settings_config_path = config_path
    app.state.settings_environment = None
    app.state.settings_sources = None
    app.state.version_environment = environment
    app.state.build_metadata = None
    app.state.project_root = project_root
    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(settings_router)
    app.include_router(system_router)
    app.include_router(health_router)
    _install_spa_routes(app, resolved_frontend_dist)
    return app


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

        raw_path = unquote(request.scope.get("raw_path", b"").decode("latin-1"))
        decoded_parts = Path(raw_path).parts
        spa_path = request.url.path.lstrip("/")
        if ".." in decoded_parts or spa_path in {"api", "health"}:
            return response
        if spa_path.startswith(("api/", "health/")):
            return response

        relative_path = Path(spa_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return response
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            return response
        if candidate.is_file():
            return FileResponse(candidate)
        if spa_path.startswith("assets/"):
            return response
        return FileResponse(index, media_type="text/html")


def _build_default_auth_service(settings: AppSettings | None = None) -> tuple[AuthService, Engine]:
    project_root = Path(__file__).resolve().parents[4]
    settings = settings or load_settings(project_root / "src/config/app.json", project_root, os.environ)
    engine = build_engine(settings.database.path)
    try:
        create_schema(engine)
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


def _administration_service_for(auth_service: object) -> UserAdministrationService | None:
    if not isinstance(auth_service, AuthService) or not isinstance(auth_service.user_sync, UserSyncService):
        return None
    return UserAdministrationService(
        user_sync=auth_service.user_sync,
        sessions=auth_service.sessions,
        password_hasher=auth_service.password_hasher,
    )


app = create_app()
