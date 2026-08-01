from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from policy_analysis.auth.models import SessionRecord, User
from policy_analysis.auth.permissions import all_page_codes
from policy_analysis.main import create_app
from sqlalchemy import select


def test_platform_authentication_and_permissions_smoke_flow(
    client_context: Callable[..., AbstractContextManager[TestClient]],
    project_root: Path,
) -> None:
    administrator_password = "admin123"
    password_contents = f"admin:{administrator_password}:admin\n"
    test_api_key = "test-webfetch-key-must-not-leak"
    test_session_secret = "test-session-secret-must-not-leak"
    runtime_directory = project_root / "runtime"
    runtime_directory.mkdir()
    password_file = runtime_directory / "password.txt"
    password_file.write_text(password_contents, encoding="utf-8")
    os.chmod(password_file, 0o600)

    config_path = project_root / "config" / "app.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "server": {"port": 30080},
                "database": {"path": "runtime/app.sqlite3"},
                "auth": {"password_file": "runtime/password.txt"},
                "webfetch": {"base_url": "https://fetch.example.invalid"},
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "POLICY_ANALYSIS_VERSION": "v0.smoke",
        "POLICY_ANALYSIS_COMMIT_SHA": "123456789abcdef",
        "POLICY_ANALYSIS_AUTH__SESSION_SECRET": test_session_secret,
        "POLICY_ANALYSIS_WEBFETCH__API_KEY": test_api_key,
    }
    app = create_app(
        project_root=project_root,
        config_path=Path("config/app.json"),
        environment=environment,
    )

    assert app.state.project_root == project_root.resolve()
    assert app.state.settings_config_path == config_path.resolve()
    assert app.state.version_environment == environment

    with client_context(app) as client:
        assert app.state.settings.database.path == runtime_directory / "app.sqlite3"
        assert app.state.settings.auth.password_file == password_file
        assert app.state.settings_environment == environment

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
        assert system_payload["version"] == "v0.smoke"
        assert system_payload["commit_sha"] == "1234567"
        assert system_payload["timezone"] == "Asia/Shanghai"
        server_time = datetime.fromisoformat(system_payload["server_time"])
        assert server_time.tzinfo is not None
        assert server_time.utcoffset() == timedelta(hours=8)
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

        token_hash = hashlib.sha256(session_token.encode()).hexdigest()
        with app.state.database_sessions() as database:
            administrator = database.scalar(select(User).where(User.username == "admin"))
            assert administrator is not None
            password_hash = administrator.password_hash
            assert password_hash.startswith("$argon2")
            session = database.scalar(select(SessionRecord).where(SessionRecord.token_hash == token_hash))
            assert session is not None
            session_id = session.id

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout.status_code == 204
        with app.state.database_sessions() as database:
            assert database.get(SessionRecord, session_id) is None

        client.cookies.set("session", session_token, path="/")
        replayed_session = client.get("/api/v1/auth/me")
        assert replayed_session.status_code == 401
        assert replayed_session.json()["error"]["code"] == "SESSION_INVALID"

        login_for_leak_check = dict(login_payload)
        login_for_leak_check["csrf_token"] = "<allowed-login-field>"
        response_bodies = json.dumps(
            [
                login_for_leak_check,
                me.json(),
                system_payload,
                settings_payload,
                logout.text,
                replayed_session.json(),
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
            token_hash,
            csrf_token,
        ):
            assert secret not in response_bodies

    assert (runtime_directory / "app.sqlite3").is_file()
