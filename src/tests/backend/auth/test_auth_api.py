import hashlib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import PagePermission, SessionRecord, User
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker


def _login(client: TestClient, username: str = "admin", password: str = "admin123"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _public_error(response) -> dict[str, object]:
    error = response.json()["error"]
    return {
        "code": error["code"],
        "message": error["message"],
        "details": error["details"],
    }


def test_login_me_csrf_and_logout(client: TestClient, password_file: Path) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    login = _login(client)

    assert login.status_code == 200
    assert login.json()["user"]["username"] == "admin"
    set_cookie = login.headers["set-cookie"]
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie
    csrf_token = login.json()["csrf_token"]

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    rejected = client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "CSRF_INVALID"

    logout = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_wrong_password_unknown_user_and_inactive_user_share_public_error(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    wrong_password = _login(client, password="wrong-password")
    unknown_user = _login(client, username="missing-user", password="wrong-password")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert _public_error(wrong_password) == _public_error(unknown_user)

    assert _login(client).status_code == 200
    password_file.write_text("", encoding="utf-8")
    inactive_user = _login(client)
    assert inactive_user.status_code == 401
    assert _public_error(inactive_user) == _public_error(wrong_password)


def test_session_and_csrf_tokens_are_random_but_only_hashes_are_persisted(
    client: TestClient,
    password_file: Path,
    database_sessions: sessionmaker[Session],
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    login = _login(client)

    assert login.status_code == 200
    session_token = client.cookies.get("session")
    csrf_token = login.json()["csrf_token"]
    assert session_token is not None
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", session_token)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf_token)
    with database_sessions() as database:
        record = database.scalar(select(SessionRecord))
        assert record is not None
        assert record.token_hash == hashlib.sha256(session_token.encode()).hexdigest()
        assert record.csrf_token_hash == hashlib.sha256(csrf_token.encode()).hexdigest()
        assert session_token not in (record.token_hash, record.csrf_token_hash)
        assert csrf_token not in (record.token_hash, record.csrf_token_hash)
        assert record.expires_at.tzinfo is not None


def test_secure_cookie_configuration_changes_only_secure_attribute(
    auth_app_factory: Callable[..., FastAPI],
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    app = auth_app_factory(secure_cookie=True)

    with client_context(app, base_url="https://testserver") as secure_client:
        login = _login(secure_client)

    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"]
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_expired_and_invalid_sessions_share_safe_unauthorized_response(
    client: TestClient,
    password_file: Path,
    mutable_clock,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    login = _login(client)
    assert login.status_code == 200
    raw_token = client.cookies.get("session")
    assert raw_token is not None

    mutable_clock.advance(seconds=12 * 60 * 60 + 1)
    expired = client.get("/api/v1/auth/me")
    client.cookies.set("session", "invalid-session-token")
    invalid = client.get("/api/v1/auth/me")

    assert expired.status_code == invalid.status_code == 401
    assert _public_error(expired) == _public_error(invalid)
    assert raw_token not in expired.text
    assert "invalid-session-token" not in invalid.text


def test_wrong_csrf_is_rejected_and_logout_invalidates_the_database_session(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    login = _login(client)
    assert login.status_code == 200
    session_token = client.cookies.get("session")
    assert session_token is not None

    wrong_csrf = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "wrong-csrf-token"},
    )
    assert wrong_csrf.status_code == 403
    assert wrong_csrf.json()["error"]["code"] == "CSRF_INVALID"
    assert "wrong-csrf-token" not in wrong_csrf.text

    logout = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert logout.status_code == 204
    assert "Path=/" in logout.headers["set-cookie"]
    client.cookies.set("session", session_token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_validation_errors_use_safe_envelope_and_request_id(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    oversized_username = "u" * 101
    oversized_password = "p" * 201

    response = client.post(
        "/api/v1/auth/login",
        json={"username": oversized_username, "password": oversized_password},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "请求参数无效。"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert error["request_id"]
    assert oversized_username not in response.text
    assert oversized_password not in response.text


def test_error_request_ids_are_unique_and_do_not_echo_invalid_cookie(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    first = _login(client, password="wrong-password")
    client.cookies.set("session", "invalid-session-token")
    second = client.get("/api/v1/auth/me")

    first_id = first.json()["error"]["request_id"]
    second_id = second.json()["error"]["request_id"]
    assert first_id == first.headers["X-Request-ID"]
    assert second_id == second.headers["X-Request-ID"]
    assert first_id != second_id
    assert "invalid-session-token" not in second.text


def test_consecutive_login_failures_return_rate_limit_error(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    attempts = [_login(client, password="wrong-password") for _ in range(3)]
    limited = _login(client, password="wrong-password")

    assert [response.status_code for response in attempts] == [401, 401, 401]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "LOGIN_RATE_LIMITED"
    assert limited.json()["error"]["request_id"] == limited.headers["X-Request-ID"]


def test_successful_login_clears_only_the_matching_failure_window(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    assert _login(client, password="wrong-password").status_code == 401
    assert _login(client, password="wrong-password").status_code == 401

    assert _login(client).status_code == 200

    after_success = [_login(client, password="wrong-password") for _ in range(3)]
    limited = _login(client, password="wrong-password")
    assert [response.status_code for response in after_success] == [401, 401, 401]
    assert limited.status_code == 429


def test_rate_limit_uses_raw_peer_address_and_normalized_account_identifier(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    attempts = [
        (" Admin ", "203.0.113.1"),
        ("ADMIN", "203.0.113.2"),
        ("admin", "203.0.113.3"),
    ]

    responses = [
        _login_with_forwarded_address(client, username, forwarded_address)
        for username, forwarded_address in attempts
    ]
    limited = _login_with_forwarded_address(client, "AdMiN", "203.0.113.4")

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert limited.status_code == 429


def test_rate_limit_is_scoped_to_the_raw_peer_and_account_pair(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    with (
        client_context(auth_app, address="198.51.100.10") as first_peer,
        client_context(auth_app, address="198.51.100.11") as second_peer,
    ):
        for _ in range(3):
            assert _login(first_peer, password="wrong-password").status_code == 401

        assert _login(second_peer, password="wrong-password").status_code == 401
        assert _login(first_peer, username="different-account", password="wrong-password").status_code == 401
        assert _login(first_peer, password="wrong-password").status_code == 429


def test_expired_rate_limit_window_is_removed_without_sleeping(
    client: TestClient,
    password_file: Path,
    mutable_clock,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    for _ in range(3):
        assert _login(client, password="wrong-password").status_code == 401
    assert _login(client, password="wrong-password").status_code == 429

    mutable_clock.advance(seconds=61)

    assert _login(client).status_code == 200


def test_unknown_account_is_rate_limited_with_the_same_counting_rule(
    client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    attempts = [_login(client, username="missing-user") for _ in range(3)]
    limited = _login(client, username="missing-user")

    assert [response.status_code for response in attempts] == [401, 401, 401]
    assert limited.status_code == 429


def test_password_file_sync_failure_returns_safe_error_without_credential_content(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
) -> None:
    secret_password = "credential-content-must-not-leak"
    password_file.write_text(
        f"admin:{secret_password}:invalid-role\n",
        encoding="utf-8",
    )

    with client_context(auth_app, raise_server_exceptions=False) as safe_client:
        response = _login(safe_client, password=secret_password)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "AUTH_SYNC_FAILED"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert secret_password not in response.text
    assert str(password_file) not in response.text


def test_unknown_api_route_uses_error_envelope_and_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/unknown-resource")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert error["details"] == {}


def test_unexpected_database_error_does_not_expose_sql_or_credentials(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
    database_sessions: sessionmaker[Session],
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    with client_context(auth_app, raise_server_exceptions=False) as safe_client:
        assert _login(safe_client).status_code == 200
        with database_sessions() as database:
            database.execute(text("DROP TABLE sessions"))
            database.commit()

        response = _login(safe_client)

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "sessions" not in response.text
    assert "admin123" not in response.text
    assert str(password_file) not in response.text


def test_me_returns_real_page_permissions_without_detached_database_objects(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
    database_sessions: sessionmaker[Session],
) -> None:
    password_file.write_text("reader:reader123:user\n", encoding="utf-8")
    with client_context(auth_app, raise_server_exceptions=False) as safe_client:
        assert _login(safe_client, username="reader", password="reader123").status_code == 200
        with database_sessions() as database:
            reader = database.scalar(select(User).where(User.username == "reader"))
            assert reader is not None
            database.add_all(
                [
                    PagePermission(user_id=reader.id, page_code="tasks"),
                    PagePermission(user_id=reader.id, page_code="policies"),
                ]
            )
            database.commit()

        response = safe_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["page_permissions"] == ["policies", "tasks"]


def test_concurrent_first_logins_share_one_atomic_password_file_sync(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    worker_count = 6
    barrier = Barrier(worker_count)

    with client_context(auth_app, raise_server_exceptions=False) as shared_client:

        def login_together() -> int:
            barrier.wait()
            return _login(shared_client).status_code

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            statuses = list(executor.map(lambda _index: login_together(), range(worker_count)))

    assert statuses == [200] * worker_count


def test_concurrent_failures_cannot_burst_past_the_configured_limit(
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
    password_file: Path,
) -> None:
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    worker_count = 8
    barrier = Barrier(worker_count)

    with client_context(auth_app, raise_server_exceptions=False) as shared_client:
        assert _login(shared_client).status_code == 200

        def fail_together() -> int:
            barrier.wait()
            return _login(shared_client, password="wrong-password").status_code

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            statuses = list(executor.map(lambda _index: fail_together(), range(worker_count)))

    assert statuses.count(401) == 3
    assert statuses.count(429) == worker_count - 3


def _login_with_forwarded_address(
    client: TestClient,
    username: str,
    forwarded_address: str,
):
    return client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": forwarded_address},
        json={"username": username, "password": "wrong-password"},
    )
