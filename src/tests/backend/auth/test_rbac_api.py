import json
import os
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import User
from policy_analysis.auth.password_file import (
    PasswordFileOperationError,
    parse_password_text,
)
from policy_analysis.auth.permissions import PageCode, can_access, require_page
from policy_analysis.auth.service import (
    PublicUser,
    UserAdministrationError,
    UserSyncService,
    _file_contains_attempted_update,
    _file_restored_previous,
)
from policy_analysis.core.errors import APIError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

ALL_PAGES = ["analysis", "policies", "push", "settings", "tasks", "users"]


def _csrf(client: TestClient) -> dict[str, str]:
    return client.csrf_headers  # type: ignore[attr-defined, no-any-return]


def _serialized(response) -> str:
    return json.dumps(response.json(), ensure_ascii=False)


def test_admin_always_receives_all_closed_page_codes(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["page_permissions"] == ALL_PAGES


def test_user_management_requires_admin_and_csrf(
    admin_client: TestClient,
    user_client: TestClient,
) -> None:
    without_csrf = admin_client.post(
        "/api/v1/users",
        json={
            "username": "analyst",
            "password": "safe-password",
            "role": "user",
            "pages": ["policies"],
        },
    )
    assert without_csrf.status_code == 403

    created = admin_client.post(
        "/api/v1/users",
        json={
            "username": "analyst",
            "password": "safe-password",
            "role": "user",
            "pages": ["policies"],
        },
        headers=_csrf(admin_client),
    )
    assert created.status_code == 201
    assert created.json()["pages"] == ["policies"]
    assert "safe-password" not in _serialized(created)
    assert "password" not in created.json()
    assert "password_hash" not in created.json()

    assert user_client.get("/api/v1/users", headers=_csrf(user_client)).status_code == 403
    assert user_client.get("/api/v1/settings/effective", headers=_csrf(user_client)).status_code == 403
    assert user_client.get("/api/v1/system/info").status_code == 200

    listed = admin_client.get("/api/v1/users", headers=_csrf(admin_client))
    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()["items"]} >= {"admin", "analyst", "reader"}
    assert "safe-password" not in _serialized(listed)
    assert "password_hash" not in _serialized(listed)


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        ("password", {"password": "changed-safe-password"}),
        ("role", {"role": "admin"}),
        ("status", {"is_active": False}),
        ("pages", {"pages": ["policies", "tasks"]}),
    ],
)
def test_each_user_mutation_requires_admin_and_csrf(
    admin_client: TestClient,
    user_client: TestClient,
    suffix: str,
    payload: dict[str, object],
) -> None:
    path = f"/api/v1/users/admin/{suffix}"

    assert admin_client.patch(path, json=payload).status_code == 403
    assert user_client.patch(path, json=payload, headers=_csrf(user_client)).status_code == 403


def test_user_management_mutations_have_explicit_paths_and_safe_responses(
    admin_client: TestClient,
    auth_app: FastAPI,
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    create = admin_client.post(
        "/api/v1/users",
        json={
            "username": "analyst",
            "password": "initial-safe-password",
            "role": "user",
            "pages": ["policies"],
        },
        headers=_csrf(admin_client),
    )
    assert create.status_code == 201

    pages = admin_client.patch(
        "/api/v1/users/analyst/pages",
        json={"pages": ["tasks", "policies", "tasks"]},
        headers=_csrf(admin_client),
    )
    assert pages.status_code == 200
    assert pages.json()["pages"] == ["policies", "tasks"]

    invalid_pages = admin_client.patch(
        "/api/v1/users/analyst/pages",
        json={"pages": ["policies", "not-a-page"]},
        headers=_csrf(admin_client),
    )
    assert invalid_pages.status_code == 422

    changed_password = admin_client.patch(
        "/api/v1/users/analyst/password",
        json={"password": "changed-safe-password"},
        headers=_csrf(admin_client),
    )
    assert changed_password.status_code == 200
    assert "changed-safe-password" not in _serialized(changed_password)

    promoted = admin_client.patch(
        "/api/v1/users/analyst/role",
        json={"role": "admin"},
        headers=_csrf(admin_client),
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    assert promoted.json()["pages"] == ALL_PAGES

    with client_context(auth_app) as analyst_client:
        login = analyst_client.post(
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "changed-safe-password"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["page_permissions"] == ALL_PAGES

    disabled = admin_client.patch(
        "/api/v1/users/analyst/status",
        json={"is_active": False},
        headers=_csrf(admin_client),
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    with client_context(auth_app) as disabled_client:
        assert (
            disabled_client.post(
                "/api/v1/auth/login",
                json={"username": "analyst", "password": "changed-safe-password"},
            ).status_code
            == 401
        )

    enabled = admin_client.patch(
        "/api/v1/users/analyst/status",
        json={"is_active": True},
        headers=_csrf(admin_client),
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True
    assert admin_client.put("/api/v1/users/analyst/role", json={"role": "user"}).status_code == 405


def test_password_file_and_database_roll_back_together_when_database_commit_fails(
    admin_client: TestClient,
    password_file: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = password_file.read_bytes()
    session_class = database_sessions.class_
    real_commit = session_class.commit
    commit_calls = 0

    def injected_commit(session: Session) -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("database failure contains credential-that-must-not-leak")
        real_commit(session)

    monkeypatch.setattr(session_class, "commit", injected_commit)

    response = admin_client.post(
        "/api/v1/users",
        json={
            "username": "rollback-user",
            "password": "credential-that-must-not-leak",
            "role": "user",
            "pages": ["policies"],
        },
        headers=_csrf(admin_client),
    )

    assert response.status_code == 503
    assert "credential-that-must-not-leak" not in response.text
    assert str(password_file) not in response.text
    assert password_file.read_bytes() == original
    with database_sessions() as database:
        assert database.scalar(select(User).where(User.username == "rollback-user")) is None


def test_concurrent_management_writes_preserve_every_password_entry(
    admin_client: TestClient,
    password_file: Path,
) -> None:
    worker_count = 6
    barrier = Barrier(worker_count)

    def create_user(index: int) -> int:
        barrier.wait()
        response = admin_client.post(
            "/api/v1/users",
            json={
                "username": f"parallel-{index}",
                "password": f"parallel-safe-{index}",
                "role": "user",
                "pages": ["policies"],
            },
            headers=_csrf(admin_client),
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        statuses = list(executor.map(create_user, range(worker_count)))

    assert statuses == [201] * worker_count
    entries = parse_password_text(password_file.read_text(encoding="utf-8"))
    assert {entry.username for entry in entries} >= {f"parallel-{index}" for index in range(worker_count)}


def test_page_permissions_gate_backend_dependency(admin_client: TestClient) -> None:
    created = admin_client.post(
        "/api/v1/users",
        json={
            "username": "reader-no-settings",
            "password": "reader-safe-password",
            "role": "user",
            "pages": ["policies"],
        },
        headers=_csrf(admin_client),
    )
    assert created.status_code == 201


def test_duplicate_and_missing_users_return_stable_public_errors(admin_client: TestClient) -> None:
    payload = {
        "username": "duplicate-user",
        "password": "duplicate-safe-password",
        "role": "user",
        "pages": [],
    }
    assert admin_client.post("/api/v1/users", json=payload, headers=_csrf(admin_client)).status_code == 201

    duplicate = admin_client.post("/api/v1/users", json=payload, headers=_csrf(admin_client))
    missing = admin_client.patch(
        "/api/v1/users/missing-user/password",
        json={"password": "missing-safe-password"},
        headers=_csrf(admin_client),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USER_EXISTS"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "USER_NOT_FOUND"
    assert "duplicate-safe-password" not in duplicate.text
    assert "missing-safe-password" not in missing.text


def test_file_durability_failure_restores_exact_original_and_rolls_back_database(
    admin_client: TestClient,
    password_file: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = password_file.read_bytes()
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_update_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError("file-failure-secret-must-not-leak")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_update_directory_fsync)

    response = admin_client.post(
        "/api/v1/users",
        json={
            "username": "file-rollback-user",
            "password": "file-failure-secret-must-not-leak",
            "role": "user",
            "pages": ["policies"],
        },
        headers=_csrf(admin_client),
    )

    assert response.status_code == 503
    assert "file-failure-secret-must-not-leak" not in response.text
    assert password_file.read_bytes() == original
    with database_sessions() as database:
        assert database.scalar(select(User).where(User.username == "file-rollback-user")) is None


def test_structured_password_file_states_drive_recovery_without_message_matching(tmp_path: Path) -> None:
    target = tmp_path / "password.txt"
    persisted = PasswordFileOperationError("任意文案", "persisted", None, ())
    pending = PasswordFileOperationError(
        "另一任意文案",
        "replaced_pending_durability",
        None,
        (),
        (target,),
    )
    restored = PasswordFileOperationError("完全不同的文案", "restored", None, ())

    assert _file_contains_attempted_update(persisted) is True
    assert _file_contains_attempted_update(BaseExceptionGroup("group", [pending])) is True
    assert _file_contains_attempted_update(restored) is False
    assert _file_restored_previous(restored) is True
    assert _file_restored_previous(OSError("not structured")) is False


def test_closed_page_dependency_accepts_admin_or_grant_and_rejects_other_users() -> None:
    admin = PublicUser(1, "admin", "admin", ())
    granted = PublicUser(2, "granted", "user", ("settings",))
    rejected = PublicUser(3, "rejected", "user", ("policies",))
    dependency = require_page(PageCode.SETTINGS)

    assert can_access("admin", set(), PageCode.SETTINGS) is True
    assert dependency(current_user=admin) is admin
    assert dependency(current_user=granted) is granted
    with pytest.raises(APIError) as denied:
        dependency(current_user=rejected)
    assert denied.value.status_code == 403


def test_password_and_role_changes_reject_database_only_users(
    admin_client: TestClient,
    password_file: Path,
) -> None:
    for username in ("missing-password-source", "missing-role-source"):
        created = admin_client.post(
            "/api/v1/users",
            json={
                "username": username,
                "password": "database-only-safe-password",
                "role": "user",
                "pages": [],
            },
            headers=_csrf(admin_client),
        )
        assert created.status_code == 201

    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")

    password_response = admin_client.patch(
        "/api/v1/users/missing-password-source/password",
        json={"password": "replacement-safe-password"},
        headers=_csrf(admin_client),
    )
    role_response = admin_client.patch(
        "/api/v1/users/missing-role-source/role",
        json={"role": "admin"},
        headers=_csrf(admin_client),
    )
    status_response = admin_client.patch(
        "/api/v1/users/missing-role-source/status",
        json={"is_active": True},
        headers=_csrf(admin_client),
    )
    pages_response = admin_client.patch(
        "/api/v1/users/not-even-in-database/pages",
        json={"pages": ["tasks"]},
        headers=_csrf(admin_client),
    )

    assert password_response.status_code == 404
    assert role_response.status_code == 404
    assert status_response.status_code == 404
    assert pages_response.status_code == 404


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        ("status", {"is_active": False}),
        ("pages", {"pages": ["tasks"]}),
    ],
)
def test_database_failure_rolls_back_status_and_page_updates(
    admin_client: TestClient,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    payload: dict[str, object],
) -> None:
    session_class = database_sessions.class_
    real_commit = session_class.commit
    commit_calls = 0

    def fail_management_commit(session: Session) -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("management-database-secret")
        real_commit(session)

    monkeypatch.setattr(session_class, "commit", fail_management_commit)

    response = admin_client.patch(
        f"/api/v1/users/admin/{suffix}",
        json=payload,
        headers=_csrf(admin_client),
    )

    assert response.status_code == 503
    assert "management-database-secret" not in response.text


def test_setting_the_existing_password_is_a_valid_idempotent_update(admin_client: TestClient) -> None:
    created = admin_client.post(
        "/api/v1/users",
        json={
            "username": "idempotent-password-user",
            "password": "same-safe-password",
            "role": "user",
            "pages": [],
        },
        headers=_csrf(admin_client),
    )
    assert created.status_code == 201

    response = admin_client.patch(
        "/api/v1/users/idempotent-password-user/password",
        json={"password": "same-safe-password"},
        headers=_csrf(admin_client),
    )

    assert response.status_code == 200
    assert "same-safe-password" not in response.text


def test_administrative_deactivation_survives_a_new_sync_service(
    admin_client: TestClient,
    password_file: Path,
    database_sessions: sessionmaker[Session],
    password_hasher,
) -> None:
    created = admin_client.post(
        "/api/v1/users",
        json={
            "username": "persistently-disabled",
            "password": "disabled-safe-password",
            "role": "user",
            "pages": [],
        },
        headers=_csrf(admin_client),
    )
    assert created.status_code == 201
    disabled = admin_client.patch(
        "/api/v1/users/persistently-disabled/status",
        json={"is_active": False},
        headers=_csrf(admin_client),
    )
    assert disabled.status_code == 200

    restarted_sync = UserSyncService(password_file, database_sessions, password_hasher)
    assert restarted_sync.sync_if_changed() is True

    with database_sessions() as database:
        user = database.scalar(select(User).where(User.username == "persistently-disabled"))
        assert user is not None
        assert user.is_active is False


def test_user_list_returns_unified_safe_error_when_password_source_is_invalid(
    admin_client: TestClient,
    password_file: Path,
) -> None:
    password_file.write_text(
        "admin:credential-source-secret:invalid-role\n",
        encoding="utf-8",
    )

    response = admin_client.get("/api/v1/users", headers=_csrf(admin_client))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "USER_ADMINISTRATION_FAILED"
    assert "credential-source-secret" not in response.text
    assert str(password_file) not in response.text


def test_administration_failure_exception_chain_does_not_retain_database_or_password_secrets(
    admin_client: TestClient,
    auth_app: FastAPI,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del admin_client
    secret = "exception-graph-secret-must-not-leak"
    session_class = database_sessions.class_

    def fail_commit(_session: Session) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(session_class, "commit", fail_commit)
    service = auth_app.state.user_administration_service

    with pytest.raises(UserAdministrationError) as raised:
        service.create_user("safe-username", secret, "user", {"policies"})

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert secret not in str(error)
    assert secret not in repr(vars(error))
