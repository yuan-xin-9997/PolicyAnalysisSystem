from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.service import AuthService, UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from policy_analysis.main import create_app
from sqlalchemy.orm import Session, sessionmaker


class MutableClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, *, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def password_file(project_root: Path) -> Path:
    path = project_root / "password.txt"
    path.write_text("", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def database_sessions(project_root: Path) -> sessionmaker[Session]:
    engine = build_engine(project_root / "app.sqlite3")
    create_schema(engine)
    return session_factory(engine)


@pytest.fixture
def password_hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


@pytest.fixture
def mutable_clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def auth_app_factory(
    database_sessions: sessionmaker[Session],
    password_file: Path,
    password_hasher: PasswordHasher,
    mutable_clock: MutableClock,
) -> Callable[..., FastAPI]:
    def build(
        *,
        secure_cookie: bool = False,
        login_attempts: int = 3,
        login_window_seconds: int = 60,
    ) -> FastAPI:
        user_sync = UserSyncService(password_file, database_sessions, password_hasher)
        auth_service = AuthService(
            sessions=database_sessions,
            user_sync=user_sync,
            password_hasher=password_hasher,
            session_hours=12,
            secure_cookie=secure_cookie,
            login_attempts=login_attempts,
            login_window_seconds=login_window_seconds,
            now=mutable_clock.now,
            monotonic=mutable_clock.monotonic,
        )
        return create_app(auth_service=auth_service)

    return build


@pytest.fixture
def auth_app(auth_app_factory: Callable[..., FastAPI]) -> FastAPI:
    return auth_app_factory()


@pytest.fixture
def client_context() -> Callable[..., AbstractContextManager[TestClient]]:
    @contextmanager
    def open_client(
        app: FastAPI,
        *,
        address: str = "198.51.100.10",
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
    ) -> Iterator[TestClient]:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Using `httpx` with `starlette.testclient` is deprecated",
                category=DeprecationWarning,
            )
            with TestClient(
                app,
                client=(address, 50000),
                base_url=base_url,
                raise_server_exceptions=raise_server_exceptions,
            ) as test_client:
                yield test_client

    return open_client


@pytest.fixture
def client(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> Iterator[TestClient]:
    with client_context(auth_app) as test_client:
        yield test_client


@pytest.fixture
def admin_client(client: TestClient, password_file: Path) -> TestClient:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    return client


@pytest.fixture
def user_client(client: TestClient, password_file: Path) -> TestClient:
    password_file.write_text("reader:reader123:user\n", encoding="utf-8")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "reader123"},
    )
    assert response.status_code == 200
    return client
