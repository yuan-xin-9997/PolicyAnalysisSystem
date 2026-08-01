from datetime import datetime
from pathlib import Path

import policy_analysis.system.routes as system_routes
import pytest
from fastapi.testclient import TestClient
from policy_analysis.system.routes import resolve_build_metadata
from sqlalchemy.orm import Session, sessionmaker


def test_system_info_uses_injected_build_metadata_and_beijing_time(
    admin_client: TestClient,
    auth_app,
) -> None:
    auth_app.state.version_environment = {
        "POLICY_ANALYSIS_VERSION": "v0.456",
        "POLICY_ANALYSIS_COMMIT_SHA": "abcdef1234567890",
    }

    response = admin_client.get("/api/v1/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "v0.456"
    assert payload["commit_sha"] == "abcdef1"
    assert payload["timezone"] == "Asia/Shanghai"
    assert datetime.fromisoformat(payload["server_time"]).utcoffset().total_seconds() == 8 * 60 * 60
    assert payload["health"] == {
        "live": "ok",
        "database": "ok",
        "task_executor": "not_configured",
    }


def test_system_info_requires_authenticated_user(client: TestClient) -> None:
    assert client.get("/api/v1/system/info").status_code == 401


def test_live_is_anonymous_lightweight_and_does_not_add_wildcard_cors(client: TestClient) -> None:
    response = client.get("/health/live", headers={"Origin": "https://untrusted.example"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "access-control-allow-origin" not in response.headers


def test_ready_runs_real_sqlite_query_and_reports_executor_not_configured(
    client: TestClient,
    database_sessions: sessionmaker[Session],
    auth_app,
) -> None:
    auth_app.state.database_sessions = database_sessions

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": {"status": "ok"},
            "task_executor": {"status": "not_configured"},
        },
    }


def test_ready_returns_safe_503_when_sqlite_query_fails(
    client: TestClient,
    auth_app,
) -> None:
    class FailingSessions:
        def __call__(self):
            raise RuntimeError("database-secret-must-not-leak")

    auth_app.state.database_sessions = FailingSessions()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": {"status": "error"},
            "task_executor": {"status": "not_configured"},
        },
    }
    assert "database-secret-must-not-leak" not in response.text
    assert "webfetch" not in response.text.lower()


def test_build_metadata_uses_read_only_git_fallback() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    version, commit_sha = resolve_build_metadata({}, repository_root)

    assert version.startswith("v0.")
    assert version.removeprefix("v0.").isdigit()
    assert len(commit_sha) == 7


def test_build_metadata_has_explicit_fallback_when_git_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_routes,
        "_git_output",
        lambda *_args: (_ for _ in ()).throw(OSError("git unavailable")),
    )

    assert resolve_build_metadata({}, tmp_path) == ("v0.dev", "unknown")
