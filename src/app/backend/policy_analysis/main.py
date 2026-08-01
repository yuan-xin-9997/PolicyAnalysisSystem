from __future__ import annotations

import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import Engine

from policy_analysis.auth.routes import router as auth_router
from policy_analysis.auth.routes import users_router
from policy_analysis.auth.service import AuthService, UserAdministrationService, UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from policy_analysis.core.errors import install_error_handlers
from policy_analysis.core.settings import AppSettings, load_settings
from policy_analysis.settings.routes import router as settings_router
from policy_analysis.system.routes import health_router
from policy_analysis.system.routes import router as system_router


def create_app(auth_service: AuthService | None = None) -> FastAPI:
    project_root = Path(__file__).resolve().parents[4]
    environment = dict(os.environ)
    config_path = project_root / "src/config/app.json"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        owns_auth_service = False
        if app.state.auth_service is None:
            default_service, engine = _build_default_auth_service()
            app.state.auth_service = default_service
            owns_auth_service = True
        if isinstance(app.state.auth_service, AuthService):
            service = app.state.auth_service
            app.state.database_sessions = service.sessions
            app.state.user_administration_service = _administration_service_for(service)
            if owns_auth_service:
                app.state.settings = load_settings(config_path, project_root, environment)
                app.state.settings_environment = environment
        try:
            yield
        finally:
            if owns_auth_service:
                app.state.auth_service = None
                app.state.user_administration_service = None
                app.state.database_sessions = None
            if owns_auth_service and engine is not None:
                engine.dispose()

    app = FastAPI(title="政策分析系统", version="0.1.0", lifespan=lifespan)
    app.state.auth_service = auth_service
    app.state.database_sessions = auth_service.sessions if isinstance(auth_service, AuthService) else None
    app.state.user_administration_service = _administration_service_for(auth_service)
    app.state.settings = AppSettings()
    app.state.settings_config_path = config_path
    app.state.settings_environment = {}
    app.state.version_environment = environment
    app.state.project_root = project_root
    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(settings_router)
    app.include_router(system_router)
    app.include_router(health_router)
    return app


def _build_default_auth_service() -> tuple[AuthService, Engine]:
    project_root = Path(__file__).resolve().parents[4]
    settings = load_settings(project_root / "src/config/app.json", project_root, os.environ)
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
