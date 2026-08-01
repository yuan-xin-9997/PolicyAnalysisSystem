from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import Engine

from policy_analysis.auth.routes import router as auth_router
from policy_analysis.auth.service import AuthService, UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from policy_analysis.core.errors import install_error_handlers
from policy_analysis.core.settings import load_settings


def create_app(auth_service: AuthService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        if app.state.auth_service is None:
            default_service, engine = _build_default_auth_service()
            app.state.auth_service = default_service
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title="政策分析系统", version="0.1.0", lifespan=lifespan)
    app.state.auth_service = auth_service
    install_error_handlers(app)
    app.include_router(auth_router)
    return app


def _build_default_auth_service() -> tuple[AuthService, Engine]:
    project_root = Path(__file__).resolve().parents[4]
    settings = load_settings(project_root / "src/config/app.json", project_root, os.environ)
    engine = build_engine(settings.database.path)
    create_schema(engine)
    sessions = session_factory(engine)
    password_hasher = PasswordHasher()
    user_sync = UserSyncService(settings.auth.password_file, sessions, password_hasher)
    return (
        AuthService(
            sessions=sessions,
            user_sync=user_sync,
            password_hasher=password_hasher,
            session_hours=settings.auth.session_hours,
            secure_cookie=settings.auth.secure_cookie,
            login_attempts=settings.auth.login_attempts,
            login_window_seconds=settings.auth.login_window_seconds,
        ),
        engine,
    )


app = create_app()
