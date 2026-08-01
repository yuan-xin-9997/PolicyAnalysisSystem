import os
import stat
import tempfile
from pathlib import Path

import policy_analysis.auth.password_file as password_file_module
import pytest
from policy_analysis.auth.password_file import (
    PasswordEntry,
    PasswordFileError,
    PasswordFileLock,
    PasswordFileOperationError,
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX 文件所有权约束")
def test_private_password_file_rejects_hardlinks_and_foreign_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "password.txt"
    path.write_text("admin:admin123:admin\n", encoding="utf-8")
    os.chmod(path, 0o600)
    hardlink = tmp_path / "password-hardlink.txt"
    os.link(path, hardlink)

    with pytest.raises(PasswordFileError, match="类型或权限无效"):
        password_file_module._assert_private_regular(path)

    hardlink.unlink()
    status = path.stat()
    monkeypatch.setattr(os, "geteuid", lambda: status.st_uid + 1)
    with pytest.raises(PasswordFileError, match="类型或权限无效"):
        password_file_module._assert_private_regular_stat(status)


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


@pytest.mark.parametrize("value", ["reader\ud800", "password\ud800"])
def test_render_rejects_lone_surrogate_without_exposing_it(value: str) -> None:
    entry = (
        PasswordEntry(value, "safe-test-password", "user")
        if value.startswith("reader")
        else PasswordEntry("reader", value, "user")
    )

    with pytest.raises(ValueError) as error:
        render_password_text([entry])

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "safe-test-password" not in repr(error.value)
    _assert_exception_graph_has_no_values(error.value, (value, "safe-test-password"))


def test_encode_failure_is_converted_outside_unicode_context_without_retaining_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    username = "reader\ud800"
    password = "private-test-password\ud800"
    monkeypatch.setattr(
        password_file_module,
        "render_password_text",
        lambda _entries: f"{username}:{password}:user\n",
    )

    with pytest.raises(PasswordFileError) as raised:
        replace_password_file(path, [PasswordEntry("placeholder", "placeholder", "user")])

    error = raised.value
    inspected = (str(error), repr(error), repr(error.args), repr(vars(error)))
    assert all(username not in value and password not in value for value in inspected)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "object")
    assert not path.exists()
    _assert_exception_graph_has_no_values(
        BaseExceptionGroup("密码文件编码失败", [error]), (username, password)
    )


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

    with pytest.raises(BaseExceptionGroup, match="密码文件写入与清理失败"):
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

    with pytest.raises(BaseExceptionGroup) as error:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(error.value)
    assert any("simulated directory fsync failure" in str(node) for node in tree)
    assert _recovery_result_errors(error.value)[0].target_state == "restored"
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

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(raised.value)
    assert any("simulated directory fsync failure" in str(node) for node in tree)
    assert _recovery_result_errors(raised.value)[0].target_state == "restored"
    assert not path.exists()
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_new_target_recovery_fsync_failure_reports_absent_but_uncertain_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    real_fsync = os.fsync

    def fail_all_directory_fsyncs(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_all_directory_fsyncs)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    operation_errors = _operation_errors(raised.value)
    assert len(operation_errors) == 1
    recovery = operation_errors[0]
    assert recovery.target_state == "restored_pending_durability"
    assert recovery.backup_path is None
    assert recovery.residual_paths == ()
    assert recovery.uncertain_paths == (path,)
    assert recovery.durability_state == "uncertain"
    assert not os.path.lexists(path)
    assert not tuple(path.parent.glob(f".{path.name}.*"))


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

    with pytest.raises(BaseExceptionGroup, match="密码文件写入与清理失败"):
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


def _exception_tree(error: BaseException) -> list[BaseException]:
    result = [error]
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            result.extend(_exception_tree(nested))
    return result


def _assert_exception_graph_has_no_values(error: BaseException, forbidden_values: tuple[str, ...]) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        inspected = (str(current), repr(current), repr(current.args), repr(vars(current)))
        assert all(value not in rendered for value in forbidden_values for rendered in inspected)
        assert not hasattr(current, "object")
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def _operation_errors(error: BaseException) -> list[PasswordFileOperationError]:
    return [node for node in _exception_tree(error) if isinstance(node, PasswordFileOperationError)]


def _recovery_result_errors(error: BaseException) -> list[PasswordFileOperationError]:
    return [node for node in _operation_errors(error) if str(node) == "密码文件恢复结果"]


def _cleanup_errors_in_tree(error: BaseException) -> list[PasswordFileOperationError]:
    return [node for node in _operation_errors(error) if str(node) == "密码文件清理失败"]


@pytest.mark.parametrize(
    "cleanup_interrupt",
    [KeyboardInterrupt("backup cleanup interrupted"), SystemExit("backup cleanup interrupted")],
)
def test_recovered_backup_cleanup_interrupt_preserves_errors_and_final_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_interrupt: BaseException,
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    real_unlink = Path.unlink
    update_error = OSError("update fsync failure marker")
    directory_fsync_calls = 0

    def fail_update_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise update_error
        real_fsync(descriptor)

    def interrupt_backup_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if ".backup." in candidate.name:
            raise cleanup_interrupt
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fail_update_fsync)
    monkeypatch.setattr(Path, "unlink", interrupt_backup_unlink)

    captured: BaseException | None = None
    try:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])
    except BaseException as error:
        captured = error

    assert isinstance(captured, BaseExceptionGroup)
    tree = _exception_tree(captured)
    assert any(error is update_error for error in tree)
    assert any(error is cleanup_interrupt for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(captured)
    assert len(cleanup_errors) == 1
    cleanup = cleanup_errors[0]
    results = _recovery_result_errors(captured)
    assert len(results) == 1
    result = results[0]
    backups = tuple(path.parent.glob(f".{path.name}.backup.*"))
    assert cleanup.target_state == result.target_state == "restored"
    assert cleanup.backup_path == result.backup_path
    assert result.backup_path is not None and backups == (result.backup_path,)
    assert cleanup.residual_paths == result.residual_paths == (result.backup_path,)
    assert cleanup.uncertain_paths == result.uncertain_paths == ()
    assert result.durability_state == "durable"
    assert path.read_text(encoding="utf-8") == original
    assert not tuple(path.parent.glob(f".{path.name}.restore.*"))
    _assert_exception_graph_has_no_values(
        captured, ("reader", "previous-test-password", "updated-test-password")
    )


@pytest.mark.parametrize(
    "cleanup_interrupt",
    [KeyboardInterrupt("restore cleanup interrupted"), SystemExit("restore cleanup interrupted")],
)
def test_restore_temp_cleanup_interrupt_preserves_update_recovery_and_final_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_interrupt: BaseException,
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    update_error = OSError("update fsync failure marker")
    recovery_error = OSError("recovery replace failure marker")
    replace_calls = 0
    directory_fsync_calls = 0

    def fail_recovery_replace(source: object, destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            real_replace(source, destination)
            return
        raise recovery_error

    def interrupt_restore_cleanup_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise update_error
            if directory_fsync_calls == 3:
                raise cleanup_interrupt
        real_fsync(descriptor)

    monkeypatch.setattr(os, "replace", fail_recovery_replace)
    monkeypatch.setattr(os, "fsync", interrupt_restore_cleanup_fsync)

    captured: BaseException | None = None
    try:
        replace_password_file(path, [updated])
    except BaseException as error:
        captured = error

    assert isinstance(captured, BaseExceptionGroup)
    tree = _exception_tree(captured)
    assert any(error is update_error for error in tree)
    assert any(error is recovery_error for error in tree)
    assert any(error is cleanup_interrupt for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(captured)
    assert len(cleanup_errors) == 1
    cleanup = cleanup_errors[0]
    results = _recovery_result_errors(captured)
    assert len(results) == 1
    result = results[0]
    backups = tuple(path.parent.glob(f".{path.name}.backup.*"))
    assert cleanup.target_state == result.target_state == "replaced_pending_durability"
    assert cleanup.backup_path == result.backup_path
    assert result.backup_path is not None and backups == (result.backup_path,)
    assert cleanup.residual_paths == result.residual_paths == ()
    assert len(cleanup.uncertain_paths) == 1
    assert ".restore." in cleanup.uncertain_paths[0].name
    assert result.uncertain_paths == (path, cleanup.uncertain_paths[0])
    assert result.durability_state == "uncertain"
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert not tuple(path.parent.glob(f".{path.name}.restore.*"))
    _assert_exception_graph_has_no_values(
        captured, ("reader", "previous-test-password", "updated-test-password")
    )


@pytest.mark.parametrize(
    "cleanup_interrupt",
    [KeyboardInterrupt("persisted cleanup interrupted"), SystemExit("persisted cleanup interrupted")],
)
def test_persisted_backup_cleanup_interrupt_groups_raw_and_structured_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_interrupt: BaseException,
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_unlink = Path.unlink

    def interrupt_backup_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if ".backup." in candidate.name:
            raise cleanup_interrupt
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_backup_unlink)

    captured: BaseException | None = None
    try:
        replace_password_file(path, [updated])
    except BaseException as error:
        captured = error

    assert isinstance(captured, BaseExceptionGroup)
    tree = _exception_tree(captured)
    assert any(error is cleanup_interrupt for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(captured)
    assert len(cleanup_errors) == 1
    cleanup = cleanup_errors[0]
    assert cleanup.target_state == "persisted"
    assert cleanup.backup_path is not None and cleanup.backup_path.exists()
    assert cleanup.residual_paths == (cleanup.backup_path,)
    assert cleanup.uncertain_paths == ()
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert not _recovery_result_errors(captured)
    _assert_exception_graph_has_no_values(
        captured, ("reader", "previous-test-password", "updated-test-password")
    )


def test_recovery_replace_failure_with_successful_temp_cleanup_exposes_final_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    replace_calls = 0
    directory_fsync_calls = 0

    def fail_recovery_replace(source: object, destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            real_replace(source, destination)
            return
        raise OSError("recovery replace failure marker")

    def fail_update_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError("update fsync failure marker")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "replace", fail_recovery_replace)
    monkeypatch.setattr(os, "fsync", fail_update_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [updated])

    tree = _exception_tree(raised.value)
    assert any("update fsync failure marker" in str(error) for error in tree)
    assert any("recovery replace failure marker" in str(error) for error in tree)
    results = _recovery_result_errors(raised.value)
    assert len(results) == 1
    result = results[0]
    backups = tuple(path.parent.glob(f".{path.name}.backup.*"))
    assert result.target_state == "replaced_pending_durability"
    assert result.backup_path is not None and backups == (result.backup_path,)
    assert result.residual_paths == ()
    assert result.uncertain_paths == (path,)
    assert result.durability_state == "uncertain"
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert not tuple(path.parent.glob(f".{path.name}.restore.*"))
    _assert_exception_graph_has_no_values(
        raised.value, ("reader", "previous-test-password", "updated-test-password")
    )


def test_recovery_fsync_failure_with_successful_cleanup_exposes_pending_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_update_and_recovery_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls in {2, 3}:
                raise OSError(f"directory fsync failure marker {directory_fsync_calls}")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_update_and_recovery_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(raised.value)
    assert any("directory fsync failure marker 2" in str(error) for error in tree)
    assert any("directory fsync failure marker 3" in str(error) for error in tree)
    results = _recovery_result_errors(raised.value)
    assert len(results) == 1
    result = results[0]
    backups = tuple(path.parent.glob(f".{path.name}.backup.*"))
    assert result.target_state == "restored_pending_durability"
    assert result.backup_path is not None and backups == (result.backup_path,)
    assert result.residual_paths == ()
    assert result.uncertain_paths == (path,)
    assert result.durability_state == "uncertain"
    assert path.read_text(encoding="utf-8") == original
    assert not tuple(path.parent.glob(f".{path.name}.restore.*"))
    _assert_exception_graph_has_no_values(
        raised.value, ("reader", "previous-test-password", "updated-test-password")
    )


def test_recovery_unlink_failure_without_old_target_exposes_remaining_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    real_fsync = os.fsync

    def fail_update_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("update fsync failure marker")
        real_fsync(descriptor)

    real_unlink = Path.unlink

    def fail_target_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if candidate == path:
            raise OSError("recovery unlink failure marker")
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fail_update_fsync)
    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [updated])

    tree = _exception_tree(raised.value)
    assert any("update fsync failure marker" in str(error) for error in tree)
    assert any("recovery unlink failure marker" in str(error) for error in tree)
    results = _recovery_result_errors(raised.value)
    assert len(results) == 1
    result = results[0]
    assert result.target_state == "replaced_pending_durability"
    assert result.backup_path is None
    assert result.residual_paths == ()
    assert result.uncertain_paths == (path,)
    assert result.durability_state == "uncertain"
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert not tuple(path.parent.glob(f".{path.name}.*"))
    _assert_exception_graph_has_no_values(raised.value, ("reader", "updated-test-password"))


@pytest.mark.parametrize(
    "update_error",
    [
        OSError("update fsync failure marker"),
        KeyboardInterrupt("update fsync failure marker"),
        SystemExit("update fsync failure marker"),
    ],
)
def test_successful_recovery_and_backup_cleanup_still_exposes_restored_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, update_error: BaseException
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_only_update_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise update_error
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_only_update_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(raised.value)
    assert any(error is update_error for error in tree)
    results = _recovery_result_errors(raised.value)
    assert len(results) == 1
    result = results[0]
    assert result.target_state == "restored"
    assert result.backup_path is None
    assert result.residual_paths == ()
    assert result.uncertain_paths == ()
    assert result.durability_state == "durable"
    assert path.read_text(encoding="utf-8") == original
    assert not tuple(path.parent.glob(f".{path.name}.*"))
    _assert_exception_graph_has_no_values(
        raised.value, ("reader", "previous-test-password", "updated-test-password")
    )


def test_recovery_replace_and_restore_unlink_failures_preserve_both_errors_and_real_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    real_unlink = Path.unlink
    replace_calls = 0
    directory_fsync_calls = 0

    def fail_recovery_replace(source: object, destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            real_replace(source, destination)
            return
        raise OSError("recovery replace failure marker")

    def fail_update_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError("update fsync failure marker")
        real_fsync(descriptor)

    def fail_restore_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if ".restore." in candidate.name:
            raise OSError("restore unlink failure marker")
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_recovery_replace)
    monkeypatch.setattr(os, "fsync", fail_update_directory_fsync)
    monkeypatch.setattr(Path, "unlink", fail_restore_unlink)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [updated])

    tree = _exception_tree(raised.value)
    assert any("recovery replace failure marker" in str(error) for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(raised.value)
    assert len(cleanup_errors) == 1
    assert cleanup_errors[0].target_state == "replaced_pending_durability"
    assert cleanup_errors[0].residual_paths == tuple(path.parent.glob(f".{path.name}.restore.*"))
    assert cleanup_errors[0].uncertain_paths == ()
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert cleanup_errors[0].backup_path in tuple(path.parent.glob(f".{path.name}.backup.*"))


def test_recovery_fsync_and_consumed_restore_cleanup_failures_preserve_true_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def fail_update_recovery_and_cleanup_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls in {2, 3, 4}:
                raise OSError(f"directory fsync failure marker {directory_fsync_calls}")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_update_recovery_and_cleanup_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(raised.value)
    assert any("directory fsync failure marker 3" in str(error) for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(raised.value)
    assert len(cleanup_errors) == 1
    assert cleanup_errors[0].target_state == "restored_pending_durability"
    assert cleanup_errors[0].residual_paths == ()
    assert len(cleanup_errors[0].uncertain_paths) == 1
    assert ".restore." in cleanup_errors[0].uncertain_paths[0].name
    assert path.read_text(encoding="utf-8") == original
    assert len(tuple(path.parent.glob(f".{path.name}.backup.*"))) == 1
    assert not tuple(path.parent.glob(f".{path.name}.restore.*"))


def test_recovery_replace_and_restore_cleanup_fsync_failures_preserve_both_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    replace_calls = 0
    directory_fsync_calls = 0

    def fail_recovery_replace(source: object, destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            real_replace(source, destination)
            return
        raise OSError("recovery replace failure marker")

    def fail_update_and_restore_cleanup_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls in {2, 3}:
                raise OSError(f"directory fsync failure marker {directory_fsync_calls}")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "replace", fail_recovery_replace)
    monkeypatch.setattr(os, "fsync", fail_update_and_restore_cleanup_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [updated])

    tree = _exception_tree(raised.value)
    assert any("recovery replace failure marker" in str(error) for error in tree)
    cleanup_errors = _cleanup_errors_in_tree(raised.value)
    assert len(cleanup_errors) == 1
    cleanup = cleanup_errors[0]
    assert cleanup.target_state == "replaced_pending_durability"
    assert cleanup.residual_paths == ()
    assert len(cleanup.uncertain_paths) == 1
    assert ".restore." in cleanup.uncertain_paths[0].name
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert cleanup.backup_path is not None and cleanup.backup_path.exists()


@pytest.mark.parametrize("failure_stage", ["unlink", "fsync"])
def test_durable_update_backup_cleanup_is_structured_and_matches_real_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    path = tmp_path / "password.txt"
    updated = PasswordEntry("reader", "updated-test-password", "user")
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_unlink = Path.unlink
    real_fsync = os.fsync
    directory_fsync_calls = 0

    if failure_stage == "unlink":

        def fail_backup_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
            if ".backup." in candidate.name:
                raise OSError("backup unlink failure marker")
            real_unlink(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_backup_unlink)
    else:

        def fail_backup_cleanup_fsync(descriptor: int) -> None:
            nonlocal directory_fsync_calls
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_fsync_calls += 1
                if directory_fsync_calls == 3:
                    raise OSError("backup fsync failure marker")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_backup_cleanup_fsync)

    with pytest.raises(PasswordFileOperationError) as raised:
        replace_password_file(path, [updated])

    error = raised.value
    backups = tuple(path.parent.glob(f".{path.name}.backup.*"))
    assert error.target_state == "persisted"
    assert error.backup_path is not None
    assert error.residual_paths == ((error.backup_path,) if failure_stage == "unlink" else ())
    assert error.uncertain_paths == (() if failure_stage == "unlink" else (error.backup_path,))
    assert backups == error.residual_paths
    assert parse_password_text(path.read_text(encoding="utf-8")) == [updated]
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("failure_stage", ["unlink", "fsync"])
def test_recovered_update_backup_cleanup_is_structured_and_state_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_fsync = os.fsync
    real_unlink = Path.unlink
    directory_fsync_calls = 0

    def fail_update_and_backup_cleanup_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls in {2, 4}:
                raise OSError(f"directory fsync failure marker {directory_fsync_calls}")
        real_fsync(descriptor)

    if failure_stage == "unlink":

        def fail_backup_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
            if ".backup." in candidate.name:
                raise OSError("backup unlink failure marker")
            real_unlink(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_backup_unlink)
    monkeypatch.setattr(os, "fsync", fail_update_and_backup_cleanup_fsync)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    cleanup_errors = _cleanup_errors_in_tree(raised.value)
    assert len(cleanup_errors) == 1
    cleanup = cleanup_errors[0]
    assert cleanup.target_state == "restored"
    assert cleanup.backup_path is not None
    assert cleanup.residual_paths == ((cleanup.backup_path,) if failure_stage == "unlink" else ())
    assert cleanup.uncertain_paths == (() if failure_stage == "unlink" else (cleanup.backup_path,))
    assert path.read_text(encoding="utf-8") == original
    assert cleanup.backup_path.exists() is (failure_stage == "unlink")


def test_without_posix_or_nofollow_swap_to_symlink_fails_before_external_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    external = tmp_path / "external.txt"
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    external.write_text("external:do-not-read:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    os.chmod(external, 0o600)
    real_open = os.open
    real_read_all = password_file_module._read_all
    swapped = False
    read_calls = 0

    def swap_before_open(candidate: object, flags: int, *args: object) -> int:
        nonlocal swapped
        if Path(candidate) == path and not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(external)
        return real_open(candidate, flags, *args)

    def count_reads(descriptor: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return real_read_all(descriptor)

    monkeypatch.setattr(password_file_module, "_uses_posix_file_security", lambda: False)
    monkeypatch.setattr(password_file_module, "_supports_o_nofollow", lambda: False)
    monkeypatch.setattr(os, "open", swap_before_open)
    monkeypatch.setattr(password_file_module, "_read_all", count_reads)

    with pytest.raises(PasswordFileError):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert read_calls == 0
    assert external.read_text(encoding="utf-8") == "external:do-not-read:user\n"


@pytest.mark.parametrize("main_error", [KeyboardInterrupt("stop"), SystemExit("exit")])
def test_base_exception_and_cleanup_failure_are_grouped_without_losing_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    main_error: BaseException,
) -> None:
    path = tmp_path / "password.txt"
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    real_unlink = Path.unlink

    monkeypatch.setattr(os, "fsync", lambda _descriptor: (_ for _ in ()).throw(main_error))

    def fail_internal_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
        if candidate.name.startswith(f".{path.name}."):
            raise OSError("cleanup failure marker")
        real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_internal_unlink)

    with pytest.raises(BaseExceptionGroup) as raised:
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    tree = _exception_tree(raised.value)
    assert any(error is main_error for error in tree)
    assert len(_operation_errors(raised.value)) >= 1
    _assert_exception_graph_has_no_values(
        raised.value, ("reader", "previous-test-password", "updated-test-password")
    )


def test_fdopen_construction_failure_closes_owned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")
    os.chmod(path, 0o600)
    real_mkstemp = tempfile.mkstemp
    descriptors: list[int] = []

    def capture_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = real_mkstemp(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    monkeypatch.setattr(tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(
        os, "fdopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fdopen failure"))
    )

    with pytest.raises(OSError, match="fdopen failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert path.read_text(encoding="utf-8") == original
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_cleanup_success_fsyncs_directory_and_failure_is_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    residual = tmp_path / ".password.txt.tmp.test"
    residual.write_text("safe-test-content", encoding="utf-8")
    calls: list[Path] = []
    monkeypatch.setattr(password_file_module, "_fsync_directory", lambda directory: calls.append(directory))

    assert (
        password_file_module._cleanup_errors(residual, password_file_module._UpdateState.NOT_REPLACED, None)
        == []
    )
    assert calls == [tmp_path]

    residual.write_text("safe-test-content", encoding="utf-8")
    monkeypatch.setattr(
        password_file_module,
        "_fsync_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("fsync failure")),
    )
    errors = password_file_module._cleanup_errors(
        residual, password_file_module._UpdateState.NOT_REPLACED, None
    )
    assert len(errors) == 1
    assert errors[0].target_state == "not_replaced"
    assert errors[0].residual_paths == ()
    assert errors[0].uncertain_paths == (residual,)
    assert errors[0].durability_state == "uncertain"


@pytest.mark.parametrize(
    ("name", "state"),
    [
        (".password.txt.tmp.test", password_file_module._UpdateState.NOT_REPLACED),
        (
            ".password.txt.restore.test",
            password_file_module._UpdateState.RESTORED_PENDING_DURABILITY,
        ),
        (".password.txt.backup.test", password_file_module._UpdateState.PERSISTED),
    ],
)
def test_unlinked_internal_file_with_failed_directory_fsync_is_durability_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    state: password_file_module._UpdateState,
) -> None:
    internal_path = tmp_path / name
    internal_path.write_text("safe-test-content", encoding="utf-8")
    backup_path = internal_path if ".backup." in name else None
    monkeypatch.setattr(
        password_file_module,
        "_fsync_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("fsync failure")),
    )

    errors = password_file_module._cleanup_errors(internal_path, state, backup_path)

    assert len(errors) == 1
    assert not os.path.lexists(internal_path)
    assert errors[0].residual_paths == ()
    assert errors[0].uncertain_paths == (internal_path,)
    assert errors[0].durability_state == "uncertain"


def test_non_posix_capability_skips_mode_and_directory_fsync_but_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    path.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setattr(password_file_module, "_uses_posix_file_security", lambda: False)
    monkeypatch.setattr(os, "fchmod", lambda *_args: pytest.fail("unexpected fchmod"))
    real_fsync = os.fsync

    def reject_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            pytest.fail("unexpected directory fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", reject_directory_fsync)

    replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])
    target = tmp_path / "target.txt"
    target.write_text("reader:previous-test-password:user\n", encoding="utf-8")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(PasswordFileError):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])


def test_shared_password_file_lock_rejects_symlink_without_nofollow_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_file = tmp_path / "password.txt"
    lock_path = tmp_path / ".password.txt.lock"
    external = tmp_path / "external.lock"
    external.write_text("external", encoding="utf-8")
    lock_path.symlink_to(external)
    monkeypatch.setattr(password_file_module, "_uses_posix_file_security", lambda: False)
    monkeypatch.setattr(password_file_module, "_supports_o_nofollow", lambda: False)

    with pytest.raises(PasswordFileError), PasswordFileLock(password_file):
        pytest.fail("symlink lock must never be acquired")

    assert external.read_text(encoding="utf-8") == "external"
