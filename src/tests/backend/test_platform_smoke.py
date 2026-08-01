from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import User
from policy_analysis.auth.permissions import all_page_codes
from policy_analysis.core.settings import load_settings_snapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def test_platform_authentication_and_permissions_smoke_flow(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    database_sessions: sessionmaker[Session],
    password_file: Path,
    project_root: Path,
) -> None:
    administrator_password = "admin123"
    password_contents = f"admin:{administrator_password}:admin\n"
    test_api_key = "test-webfetch-key-must-not-leak"
    test_session_secret = "test-session-secret-must-not-leak"
    password_file.write_text(password_contents, encoding="utf-8")

    config_path = project_root / "app.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"port": 30080},
                "database": {"path": "app.sqlite3"},
                "auth": {"password_file": "password.txt"},
                "webfetch": {"base_url": "https://fetch.example.invalid"},
            }
        ),
        encoding="utf-8",
    )
    snapshot = load_settings_snapshot(
        config_path,
        project_root,
        {
            "POLICY_ANALYSIS_AUTH__SESSION_SECRET": test_session_secret,
            "POLICY_ANALYSIS_WEBFETCH__API_KEY": test_api_key,
        },
    )
    assert snapshot.settings.database.path == project_root / "app.sqlite3"
    assert snapshot.settings.auth.password_file == password_file

    with client_context(auth_app) as client:
        auth_app.state.settings = snapshot.settings
        auth_app.state.settings_sources = snapshot.sources

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": administrator_password},
        )
        assert login.status_code == 200
        login_payload = login.json()
        assert set(login_payload) == {"user", "csrf_token"}
        assert login_payload["user"] == {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "page_permissions": list(all_page_codes()),
        }
        csrf_token = login_payload["csrf_token"]
        session_token = client.cookies.get("session")
        assert isinstance(csrf_token, str) and csrf_token
        assert isinstance(session_token, str) and session_token
        set_cookie = login.headers["set-cookie"]
        assert set_cookie.startswith("session=")
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie
        assert "SameSite=lax" in set_cookie

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == login_payload["user"]

        system_info = client.get("/api/v1/system/info")
        assert system_info.status_code == 200
        system_payload = system_info.json()
        assert system_payload["version"]
        assert len(system_payload["commit_sha"]) == 7
        assert system_payload["timezone"] == "Asia/Shanghai"
        assert system_payload["health"] == {
            "live": "ok",
            "database": "ok",
            "task_executor": "not_configured",
        }

        effective_settings = client.get("/api/v1/settings/effective")
        assert effective_settings.status_code == 200
        settings_payload = effective_settings.json()
        assert settings_payload["values"]["auth"]["password_file"] == "********"
        assert settings_payload["values"]["auth"]["session_secret"] == "********"
        assert settings_payload["values"]["webfetch"]["api_key"] == "********"
        assert settings_payload["sources"]["server.port"] == "config_file"
        assert settings_payload["sources"]["auth.session_secret"] == "environment"
        assert settings_payload["sources"]["webfetch.api_key"] == "environment"
        assert set(settings_payload["sources"].values()) <= {
            "default",
            "config_file",
            "environment",
        }
        assert settings_payload["webfetch"] == {"status": "configured", "checked": False}

        with database_sessions() as database:
            administrator = database.scalar(select(User).where(User.username == "admin"))
            assert administrator is not None
            password_hash = administrator.password_hash
            assert password_hash.startswith("$argon2")

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout.status_code == 204
        logged_out_me = client.get("/api/v1/auth/me")
        assert logged_out_me.status_code == 401
        assert logged_out_me.json()["error"]["code"] == "SESSION_INVALID"

        login_for_leak_check = dict(login_payload)
        login_for_leak_check["csrf_token"] = "<allowed-login-field>"
        response_bodies = json.dumps(
            [
                login_for_leak_check,
                me.json(),
                system_payload,
                settings_payload,
                logout.text,
                logged_out_me.json(),
            ],
            ensure_ascii=False,
        )
        for secret in (
            administrator_password,
            password_contents,
            password_hash,
            test_api_key,
            test_session_secret,
            session_token,
            csrf_token,
        ):
            assert secret not in response_bodies
