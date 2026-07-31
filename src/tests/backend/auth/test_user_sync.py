import os
from datetime import UTC
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from policy_analysis.auth.models import User
from policy_analysis.auth.service import PasswordSyncError, UserSyncService
from policy_analysis.core.database import build_engine, create_schema, session_factory
from sqlalchemy import select, text
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
    os.chmod(path, 0o600)
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


def test_sync_processes_new_inode_even_when_mtime_is_unchanged(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    replacement = tmp_path / "replacement.txt"
    _write_password_file(replacement, "reader:changed-test-password:user\n")
    os.utime(replacement, ns=(initial_mtime_ns, initial_mtime_ns))
    os.replace(replacement, password_file)

    assert service.sync_if_changed() is True
    assert password_hasher.verify(_user(database_sessions, "reader").password_hash, "changed-test-password")


def test_sync_retries_after_path_is_replaced_between_snapshot_steps(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    password_hasher: PasswordHasher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_file = tmp_path / "password.txt"
    _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    replacement = tmp_path / "replacement.txt"
    _write_password_file(replacement, "reader:changed-test-password:user\n")
    real_open = os.open
    replaced = False

    def replace_after_open(path: str | os.PathLike[str], flags: int, *args: object) -> int:
        nonlocal replaced
        descriptor = real_open(path, flags, *args)
        if Path(path) == password_file and not replaced:
            replaced = True
            os.replace(replacement, password_file)
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_open)

    assert service.sync_if_changed() is True
    assert password_hasher.verify(_user(database_sessions, "reader").password_hash, "changed-test-password")


def test_sync_failure_does_not_record_fingerprint_and_same_version_retries(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    password_hasher: PasswordHasher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_file = tmp_path / "password.txt"
    _write_password_file(password_file, "reader:initial-test-password:user\n")
    service = UserSyncService(password_file, database_sessions, password_hasher)
    real_open = os.open
    removed = False

    def remove_after_open(path: str | os.PathLike[str], flags: int, *args: object) -> int:
        nonlocal removed
        descriptor = real_open(path, flags, *args)
        if Path(path) == password_file and not removed:
            removed = True
            password_file.unlink()
        return descriptor

    monkeypatch.setattr(os, "open", remove_after_open)
    with pytest.raises(RuntimeError, match="读取不稳定"):
        service.sync_if_changed()
    monkeypatch.setattr(os, "open", real_open)
    _write_password_file(password_file, "reader:initial-test-password:user\n")

    assert service.sync_if_changed() is True
    assert _user(database_sessions, "reader").is_active is True


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


def test_sync_rehashes_verified_password_only_when_argon2_parameters_need_upgrade(
    tmp_path: Path, database_sessions: sessionmaker[Session]
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(password_file, "reader:initial-test-password:user\n")
    weak_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    current_hasher = PasswordHasher(time_cost=2, memory_cost=8192, parallelism=1)
    service = UserSyncService(password_file, database_sessions, weak_hasher)
    service.sync_if_changed()
    old_hash = _user(database_sessions, "reader").password_hash
    _write_password_file(password_file, "reader:initial-test-password:user\n", initial_mtime_ns)

    upgraded_service = UserSyncService(password_file, database_sessions, current_hasher)
    assert upgraded_service.sync_if_changed() is True
    upgraded_hash = _user(database_sessions, "reader").password_hash
    assert upgraded_hash != old_hash
    assert current_hasher.verify(upgraded_hash, "initial-test-password")
    upgraded_mtime_ns = password_file.stat().st_mtime_ns
    _write_password_file(password_file, "reader:initial-test-password:user\n", upgraded_mtime_ns)
    assert upgraded_service.sync_if_changed() is True
    assert _user(database_sessions, "reader").password_hash == upgraded_hash


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


def test_sync_rolls_back_all_users_and_retries_same_fingerprint_after_database_failure(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    initial_mtime_ns = _write_password_file(
        password_file, "reader:initial-test-password:user\nwriter:writer-test-password:user\n"
    )
    service = UserSyncService(password_file, database_sessions, password_hasher)
    service.sync_if_changed()
    old_reader_hash = _user(database_sessions, "reader").password_hash
    old_writer_hash = _user(database_sessions, "writer").password_hash
    _write_password_file(
        password_file,
        "reader:changed-reader-password:user\nwriter:changed-writer-password:admin\n",
        initial_mtime_ns,
    )
    with database_sessions() as session:
        session.execute(
            text(
                "CREATE TRIGGER abort_writer_update BEFORE UPDATE ON users "
                "WHEN NEW.username = 'writer' BEGIN SELECT RAISE(ABORT, 'test abort'); END"
            )
        )
        session.commit()

    with pytest.raises(PasswordSyncError) as error:
        service.sync_if_changed()

    assert "changed-reader-password" not in str(error.value)
    assert "changed-writer-password" not in repr(error.value)
    assert "argon2" not in repr(error.value).lower()
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert vars(error.value) == {}

    assert _user(database_sessions, "reader").password_hash == old_reader_hash
    assert _user(database_sessions, "writer").password_hash == old_writer_hash
    with database_sessions() as session:
        session.execute(text("DROP TRIGGER abort_writer_update"))
        session.commit()

    assert service.sync_if_changed() is True
    assert password_hasher.verify(_user(database_sessions, "reader").password_hash, "changed-reader-password")
    writer = _user(database_sessions, "writer")
    assert password_hasher.verify(writer.password_hash, "changed-writer-password")
    assert writer.role == "admin"


def test_sync_rejects_insecure_permissions_symlink_and_fifo_before_reading(
    tmp_path: Path, database_sessions: sessionmaker[Session], password_hasher: PasswordHasher
) -> None:
    password_file = tmp_path / "password.txt"
    _write_password_file(password_file, "reader:initial-test-password:user\n")
    os.chmod(password_file, 0o644)
    service = UserSyncService(password_file, database_sessions, password_hasher)
    with pytest.raises(RuntimeError, match="凭据文件"):
        service.sync_if_changed()

    target = tmp_path / "target.txt"
    _write_password_file(target, "reader:initial-test-password:user\n")
    password_file.unlink()
    password_file.symlink_to(target)
    with pytest.raises(RuntimeError, match="凭据文件"):
        service.sync_if_changed()
    assert target.read_text(encoding="utf-8") == "reader:initial-test-password:user\n"

    password_file.unlink()
    os.mkfifo(password_file)
    with pytest.raises(RuntimeError, match="凭据文件"):
        service.sync_if_changed()
