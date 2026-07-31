import os
import stat
import tempfile
from pathlib import Path

import pytest
from policy_analysis.auth.password_file import (
    PasswordEntry,
    PasswordFileError,
    parse_password_text,
    render_password_text,
    replace_password_file,
)


def test_parse_ignores_comments_and_preserves_valid_roles() -> None:
    text = "# comment\nadmin:admin123:admin\nreader:read123:user\n"

    assert parse_password_text(text) == [
        PasswordEntry("admin", "admin123", "admin"),
        PasswordEntry("reader", "read123", "user"),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("missing-fields\n", "格式无效"),
        (":password:user\n", "内容无效"),
        ("reader::user\n", "内容无效"),
        ("reader:password:operator\n", "内容无效"),
        ("reader:first:user\nreader:second:user\n", "内容无效"),
    ],
)
def test_parse_rejects_invalid_or_duplicate_entries(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_password_text(text)


@pytest.mark.parametrize(
    "entry",
    [
        PasswordEntry("reader:admin", "safe-test-password", "user"),
        PasswordEntry("reader", "safe:test-password", "user"),
        PasswordEntry("reader", "line-one\nline-two", "user"),
        PasswordEntry("reader", "line-one\rline-two", "user"),
        PasswordEntry("reader", "line-one\u2028line-two", "user"),
        PasswordEntry("reader", "line-one\u2029line-two", "user"),
        PasswordEntry("#reader", "safe-test-password", "user"),
        PasswordEntry(" reader", "safe-test-password", "user"),
        PasswordEntry("reader", "safe-test-password ", "user"),
        PasswordEntry("reader", "safe-test-password", "operator"),  # type: ignore[arg-type]
    ],
)
def test_render_rejects_runtime_entries_that_cannot_round_trip(entry: PasswordEntry) -> None:
    with pytest.raises(ValueError, match=r"条目 1") as error:
        render_password_text([entry])

    assert "reader" not in str(error.value)
    assert "safe" not in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "reader:bad:password:user\n",
        "#reader:safe-test-password:user\n",
        " reader:safe-test-password:user\n",
        "reader:safe-test-password :user\n",
        "reader:safe\x00test-password:user\n",
    ],
)
def test_parse_rejects_ambiguous_or_lossy_entries_without_echoing_values(text: str) -> None:
    with pytest.raises(ValueError, match=r"第 1 行") as error:
        parse_password_text(text)

    assert "reader" not in str(error.value)
    assert "safe" not in str(error.value)


def test_render_rejects_duplicates_and_parse_render_round_trips_exact_entries() -> None:
    entries = [
        PasswordEntry("reader", "safe-test-password", "user"),
        PasswordEntry("admin", "other-test-password", "admin"),
    ]

    with pytest.raises(ValueError, match=r"条目 2"):
        render_password_text([entries[0], entries[0]])

    assert parse_password_text(render_password_text(entries)) == entries


def test_render_round_trips_entries_without_exposing_old_content() -> None:
    entries = [PasswordEntry("reader", "new-password", "user")]

    rendered = render_password_text(entries)

    assert parse_password_text(rendered) == entries
    assert rendered.startswith("# 格式: username:password:role")
    assert "old-password" not in rendered


def test_replace_writes_private_file_and_uses_same_directory_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials" / "password.txt"
    replacement_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacement_calls.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    replace_password_file(path, [PasswordEntry("reader", "safe-test-password", "user")])

    assert parse_password_text(path.read_text(encoding="utf-8")) == [
        PasswordEntry("reader", "safe-test-password", "user")
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert replacement_calls == [(replacement_calls[0][0], path)]
    assert replacement_calls[0][0].parent == path.parent
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_keeps_existing_file_and_cleans_temporary_file_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)

    def failing_fsync(_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert path.read_text(encoding="utf-8") == original
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_keeps_existing_file_and_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)

    def failing_replace(
        _source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        _destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert path.read_text(encoding="utf-8") == original
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_restores_old_file_and_cleans_all_internal_files_when_directory_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_only_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_only_directory_fsync)

    with pytest.raises(OSError, match="simulated directory fsync failure") as error:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert "updated-test-password" not in str(error.value)
    assert path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_removes_new_file_when_directory_fsync_fails_without_previous_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_only_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 1:
                raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_only_directory_fsync)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert not path.exists()
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_keeps_private_backup_when_recovery_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    calls = 0
    directory_fsync_calls = 0

    def fail_update_and_recovery(source: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_replace(source, destination)
            return
        raise OSError("recovery replace failure")

    def fail_post_replace_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError("update durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "replace", fail_update_and_recovery)
    monkeypatch.setattr(os, "fsync", fail_post_replace_directory_fsync)

    with pytest.raises(BaseExceptionGroup) as error:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert "updated-test-password" not in str(error.value)
    backups = list(path.parent.glob(f".{path.name}.backup.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_replace_closes_temporary_descriptor_when_backup_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_mkstemp = tempfile.mkstemp
    descriptors: list[int] = []

    def track_backup_descriptor(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = real_mkstemp(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("backup fsync failure")

    monkeypatch.setattr(tempfile, "mkstemp", track_backup_descriptor)
    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="backup fsync failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_replace_reports_recovery_directory_fsync_failure_and_preserves_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_update_and_recovery_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls in {2, 3}:
                raise OSError("directory durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_update_and_recovery_directory_fsync)

    with pytest.raises(BaseExceptionGroup) as error:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert "updated-test-password" not in str(error.value)
    assert path.read_text(encoding="utf-8") == original
    backups = list(path.parent.glob(f".{path.name}.backup.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_replace_reports_backup_cleanup_failure_after_durable_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_unlink = Path.unlink

    def fail_only_backup_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if ".backup." in candidate.name:
            raise OSError("backup cleanup failure")
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_only_backup_unlink)

    with pytest.raises(PasswordFileError, match="清理失败") as error:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert "updated-test-password" not in str(error.value)
    assert parse_password_text(path.read_text(encoding="utf-8")) == [
        PasswordEntry("reader", "updated-test-password", "user")
    ]
    backups = list(path.parent.glob(f".{path.name}.backup.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
