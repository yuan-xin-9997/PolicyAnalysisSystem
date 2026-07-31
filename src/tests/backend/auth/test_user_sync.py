import os
from datetime import UTC
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from policy_analysis.auth.models import User
from policy_analysis.auth.service import UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def password_hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


@pytest.fixture
def database_sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    return session_factory(engine)


def _write_password_file(path: Path, text: str, previous_mtime_ns: int | None = None) -> int:
    path.write_text(text, encoding="utf-8")
    if previous_mtime_ns is not None:
        os.utime(path, ns=(previous_mtime_ns, previous_mtime_ns + 1))
    return path.stat().st_mtime_ns


def _user(sessions: sessionmaker[Session], username: str) -> User:
    with sessions() as session:
        user = session.scalar(select(User).where(User.username == username))
        assert user is not None
        session.expunge(user)
        return user


def test_sync_adds_new_user_with_real_argon2_hash_and_utc_timestamp(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)

    assert service.sync_if_changed() is True

    reader = _user(database_sessions, "reader")
    assert password_hasher.verify(reader.password_hash, "initial-test-password")
    assert reader.role == "user"
    assert reader.is_active is True
    assert reader.password_synced_at is not None
    assert reader.password_synced_at.tzinfo is not None
    assert reader.password_synced_at.utcoffset() == UTC.utcoffset(reader.password_synced_at)


def test_sync_skips_unchanged_mtime_without_rehashing(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    current_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    assert service.sync_if_changed() is True
    initial_hash = _user(database_sessions, "reader").password_hash

    _write_password_file(password_file, "reader:changed-test-password:user\n")
    os.utime(password_file, ns=(current_mtime_ns, current_mtime_ns))

    assert service.sync_if_changed() is False
    reader = _user(database_sessions, "reader")
    assert reader.password_hash == initial_hash
    assert password_hasher.verify(reader.password_hash, "initial-test-password")


def test_sync_updates_password_when_file_changes(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    original_hash = _user(database_sessions, "reader").password_hash

    _write_password_file(password_file, "reader:changed-test-password:user\n", initial_mtime_ns)

    assert service.sync_if_changed() is True
    reader = _user(database_sessions, "reader")
    assert reader.password_hash != original_hash
    assert password_hasher.verify(reader.password_hash, "changed-test-password")


def test_sync_updates_role_when_file_changes(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()

    _write_password_file(password_file, "reader:initial-test-password:admin\n", initial_mtime_ns)

    assert service.sync_if_changed() is True
    assert _user(database_sessions, "reader").role == "admin"


def test_sync_safely_rehashes_when_existing_hash_cannot_be_verified(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    with database_sessions() as session:
        reader = session.scalar(select(User).where(User.username == "reader"))
        assert reader is not None
        reader.password_hash = "invalid-hash-for-test"
        session.commit()

    _write_password_file(password_file, "reader:initial-test-password:user\n", initial_mtime_ns)

    assert service.sync_if_changed() is True
    assert password_hasher.verify(_user(database_sessions, "reader").password_hash, "initial-test-password")


def test_sync_disables_removed_user_without_deleting_record_and_reenables_it(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(
        password_file,
        "reader:initial-test-password:user\nwriter:writer-test-password:user\n",
    )
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    writer_id = _user(database_sessions, "writer").id

    removed_mtime_ns = _write_password_file(
        password_file, "reader:initial-test-password:user\n", initial_mtime_ns
    )
    assert service.sync_if_changed() is True
    removed_writer = _user(database_sessions, "writer")
    assert removed_writer.id == writer_id
    assert removed_writer.is_active is False

    _write_password_file(
        password_file,
        "reader:initial-test-password:user\nwriter:writer-test-password:user\n",
        removed_mtime_ns,
    )
    assert service.sync_if_changed() is True
    assert _user(database_sessions, "writer").is_active is True


@pytest.mark.parametrize(
    "invalid_contents",
    [
        "reader:initial-test-password:admin\nnew-user:new-test-password:user\nbroken-line\n",
        "reader:initial-test-password:admin\nreader:another-test-password:user\n",
    ],
)
def test_sync_rejects_invalid_file_without_partial_database_update(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    password_hasher: PasswordHasher,
    invalid_contents: str,
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    original_hash = _user(database_sessions, "reader").password_hash

    _write_password_file(password_file, invalid_contents, initial_mtime_ns)

    with pytest.raises(ValueError):
        service.sync_if_changed()

    reader = _user(database_sessions, "reader")
    assert reader.role == "user"
    assert reader.password_hash == original_hash
    with database_sessions() as session:
        assert session.scalar(select(User).where(User.username == "new-user")) is None
