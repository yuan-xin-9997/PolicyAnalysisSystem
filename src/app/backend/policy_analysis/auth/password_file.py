"""Parsing and safe replacement of the password credential source file."""

from __future__ import annotations

import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

Role = Literal["admin", "user"]
TargetState = Literal[
    "not_replaced",
    "replaced_pending_durability",
    "restored_pending_durability",
    "restored",
    "persisted",
]
DurabilityState = Literal["durable", "uncertain"]

_HEADER = "# 格式: username:password:role  (role 取值: admin | user)"
_ROLE_HELP = "# admin 默认拥有所有页面权限；user 的页面权限由管理员配置。"


class PasswordFileError(RuntimeError):
    """A credential-file error whose message never includes credential values."""


class PasswordFileOperationError(PasswordFileError):
    """Safe, structured operational state for controlled credential-file recovery."""

    def __init__(
        self,
        message: str,
        target_state: TargetState,
        backup_path: Path | None,
        residual_paths: tuple[Path, ...],
        uncertain_paths: tuple[Path, ...] = (),
    ):
        super().__init__(message)
        self.target_state = target_state
        self.backup_path = backup_path
        self.residual_paths = residual_paths
        self.uncertain_paths = uncertain_paths
        self.durability_state: DurabilityState = (
            "uncertain" if target_state.endswith("pending_durability") or uncertain_paths else "durable"
        )


class _UpdateState(Enum):
    NOT_REPLACED = "not_replaced"
    REPLACED_PENDING_DURABILITY = "replaced_pending_durability"
    RESTORED_PENDING_DURABILITY = "restored_pending_durability"
    RESTORED = "restored"
    PERSISTED = "persisted"


@dataclass(frozen=True, slots=True)
class _RecoveryOutcome:
    state: _UpdateState
    errors: tuple[BaseException, ...] = ()


@dataclass(frozen=True, slots=True)
class _PrivateFileFingerprint:
    device: int
    inode: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    size: int

    @classmethod
    def from_stat(cls, status: os.stat_result) -> _PrivateFileFingerprint:
        return cls(
            device=status.st_dev,
            inode=status.st_ino,
            mode=status.st_mode,
            mtime_ns=status.st_mtime_ns,
            ctime_ns=status.st_ctime_ns,
            size=status.st_size,
        )


@dataclass(frozen=True, slots=True)
class PasswordEntry:
    username: str
    password: str
    role: Role


def parse_password_text(text: str) -> list[PasswordEntry]:
    entries: list[PasswordEntry] = []
    usernames: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        if raw_line.lstrip().startswith("#"):
            if len(raw_line.lstrip().split(":")) != 3:
                continue
            raise ValueError(f"password.txt 第 {line_number} 行内容无效")
        parts = raw_line.split(":")
        if len(parts) != 3:
            raise ValueError(f"password.txt 第 {line_number} 行格式无效")
        entry = PasswordEntry(*parts)
        _validate_entry(entry, usernames, f"第 {line_number} 行")
        usernames.add(entry.username)
        entries.append(entry)
    return entries


def render_password_text(entries: list[PasswordEntry]) -> str:
    usernames: set[str] = set()
    for entry_number, entry in enumerate(entries, start=1):
        _validate_entry(entry, usernames, f"条目 {entry_number}")
        usernames.add(entry.username)
    return "\n".join([_HEADER, _ROLE_HELP, *(f"{e.username}:{e.password}:{e.role}" for e in entries)]) + "\n"


def replace_password_file(path: Path, entries: list[PasswordEntry]) -> None:
    """Replace credentials atomically, retaining a recovery copy until durable."""
    rendered_text = render_password_text(entries)
    rendered = _encode_utf8_or_none(rendered_text)
    if rendered is None:
        raise PasswordFileError("密码文件编码无效")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    temporary_path: Path | None = None
    state = _UpdateState.NOT_REPLACED
    existed = os.path.lexists(path)

    try:
        if existed:
            _assert_private_regular(path)
            backup_path = _write_private_copy(path.parent, f".{path.name}.backup.", _read_private_file(path))
            _fsync_directory(path.parent)
        temporary_path = _write_private_copy(path.parent, f".{path.name}.tmp.", rendered)
        os.replace(temporary_path, path)
        temporary_path = None
        state = _UpdateState.REPLACED_PENDING_DURABILITY
        _fsync_directory(path.parent)
        state = _UpdateState.PERSISTED
    except BaseException as original_error:
        if state is _UpdateState.REPLACED_PENDING_DURABILITY:
            recovery = _restore_previous(path, backup_path, existed)
            state = recovery.state
            if recovery.errors:
                # Keep backup_path intact: it is the only durable old version on failed recovery.
                recovery_result = _recovery_result_error(path, state, backup_path, recovery.errors)
                raise BaseExceptionGroup(
                    "密码文件更新和恢复失败",
                    [original_error, *recovery.errors, recovery_result],
                ) from None
            cleanup_errors = _cleanup_errors(backup_path, state, backup_path)
            recovery_result = _recovery_result_error(path, state, backup_path, tuple(cleanup_errors))
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "密码文件更新失败且清理失败",
                    [original_error, *cleanup_errors, recovery_result],
                ) from None
            raise BaseExceptionGroup(
                "密码文件更新失败但恢复完成", [original_error, recovery_result]
            ) from None

        cleanup_errors = _cleanup_errors(temporary_path, state, backup_path)
        if state is _UpdateState.NOT_REPLACED:
            cleanup_errors.extend(_cleanup_errors(backup_path, state, backup_path))
        if cleanup_errors:
            raise BaseExceptionGroup("密码文件操作与清理失败", [original_error, *cleanup_errors]) from None
        raise

    cleanup_errors = _cleanup_errors(backup_path, state, backup_path)
    if cleanup_errors:
        raise cleanup_errors[0] from None


def _restore_previous(path: Path, backup_path: Path | None, existed: bool) -> _RecoveryOutcome:
    if not existed:
        try:
            path.unlink(missing_ok=True)
        except BaseException as error:
            return _RecoveryOutcome(_UpdateState.REPLACED_PENDING_DURABILITY, (error,))
        try:
            _fsync_directory(path.parent)
        except BaseException as recovery_fsync_error:
            return _RecoveryOutcome(
                _UpdateState.RESTORED_PENDING_DURABILITY,
                (recovery_fsync_error,),
            )
        return _RecoveryOutcome(_UpdateState.RESTORED)

    if backup_path is None:
        return _RecoveryOutcome(
            _UpdateState.REPLACED_PENDING_DURABILITY,
            (PasswordFileError("密码文件恢复失败"),),
        )
    try:
        recovery_path = _write_private_copy(
            path.parent, f".{path.name}.restore.", _read_private_file(backup_path)
        )
    except BaseException as error:
        return _RecoveryOutcome(_UpdateState.REPLACED_PENDING_DURABILITY, (error,))

    try:
        os.replace(recovery_path, path)
    except BaseException as error:
        cleanup_errors = _cleanup_errors(recovery_path, _UpdateState.REPLACED_PENDING_DURABILITY, backup_path)
        return _RecoveryOutcome(_UpdateState.REPLACED_PENDING_DURABILITY, (error, *cleanup_errors))

    try:
        _fsync_directory(path.parent)
    except BaseException as recovery_fsync_error:
        cleanup_errors = _cleanup_errors(recovery_path, _UpdateState.RESTORED_PENDING_DURABILITY, backup_path)
        if cleanup_errors:
            return _RecoveryOutcome(
                _UpdateState.RESTORED_PENDING_DURABILITY,
                (recovery_fsync_error, *cleanup_errors),
            )
        # Preserve the designated recovery fsync failure even if cleanup's directory fsync succeeds.
        return _RecoveryOutcome(_UpdateState.RESTORED_PENDING_DURABILITY, (recovery_fsync_error,))
    return _RecoveryOutcome(_UpdateState.RESTORED)


def _recovery_result_error(
    path: Path,
    state: _UpdateState,
    backup_path: Path | None,
    related_errors: tuple[BaseException, ...],
) -> PasswordFileOperationError:
    residual_paths: list[Path] = []
    uncertain_paths: list[Path] = []
    if state in {
        _UpdateState.REPLACED_PENDING_DURABILITY,
        _UpdateState.RESTORED_PENDING_DURABILITY,
    }:
        uncertain_paths.append(path)
    for error in related_errors:
        for operation_error in _nested_operation_errors(error):
            residual_paths.extend(operation_error.residual_paths)
            uncertain_paths.extend(operation_error.uncertain_paths)
    final_residual_paths = tuple(dict.fromkeys(residual_paths))
    final_uncertain_paths = tuple(dict.fromkeys(uncertain_paths))
    final_backup_path = backup_path
    if final_backup_path is not None and not (
        os.path.lexists(final_backup_path)
        or final_backup_path in final_residual_paths
        or final_backup_path in final_uncertain_paths
    ):
        final_backup_path = None
    return PasswordFileOperationError(
        "密码文件恢复结果",
        target_state=state.value,
        backup_path=final_backup_path,
        residual_paths=final_residual_paths,
        uncertain_paths=final_uncertain_paths,
    )


def _nested_operation_errors(error: BaseException) -> tuple[PasswordFileOperationError, ...]:
    result: list[PasswordFileOperationError] = []
    if isinstance(error, PasswordFileOperationError):
        result.append(error)
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            result.extend(_nested_operation_errors(nested))
    return tuple(result)


def _read_private_file(path: Path) -> bytes:
    expected = _assert_private_regular(path)
    try:
        descriptor = os.open(path, _private_read_flags())
    except OSError as error:
        raise PasswordFileError("凭据文件不可用") from error
    try:
        opened_status = os.fstat(descriptor)
        _assert_private_regular_stat(opened_status)
        opened = _PrivateFileFingerprint.from_stat(opened_status)
        if opened != expected:
            raise PasswordFileError("凭据文件读取不稳定")
        contents = _read_all(descriptor)
        after_read_status = os.fstat(descriptor)
        _assert_private_regular_stat(after_read_status)
        after_read = _PrivateFileFingerprint.from_stat(after_read_status)
    finally:
        os.close(descriptor)
    current = _assert_private_regular(path)
    if expected != opened or opened != after_read or after_read != current:
        raise PasswordFileError("凭据文件读取不稳定")
    return contents


def _write_private_copy(directory: Path, prefix: str, contents: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, dir=directory)
    result = Path(temporary_name)
    owns_descriptor = True
    try:
        handle = os.fdopen(descriptor, "wb")
        owns_descriptor = False
        with handle:
            _set_private_mode(handle.fileno())
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException as original_error:
        if owns_descriptor:
            os.close(descriptor)
        cleanup_errors = _cleanup_errors(result, _UpdateState.NOT_REPLACED, None)
        if cleanup_errors:
            raise BaseExceptionGroup("密码文件写入与清理失败", [original_error, *cleanup_errors]) from None
        raise
    return result


def _assert_private_regular(path: Path) -> _PrivateFileFingerprint:
    try:
        status = path.lstat()
    except OSError as error:
        raise PasswordFileError("凭据文件不可用") from error
    _assert_private_regular_stat(status)
    return _PrivateFileFingerprint.from_stat(status)


def _assert_private_regular_stat(status: os.stat_result) -> None:
    if not stat.S_ISREG(status.st_mode) or (
        _uses_posix_file_security() and stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise PasswordFileError("凭据文件类型或权限无效")


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 64 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _cleanup_errors(
    path: Path | None, state: _UpdateState, backup_path: Path | None
) -> list[PasswordFileOperationError]:
    if path is None:
        return []
    try:
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
    except OSError:
        residual_paths = (path,) if os.path.lexists(path) else ()
        return [
            PasswordFileOperationError(
                "密码文件清理失败",
                target_state=state.value,
                backup_path=backup_path,
                residual_paths=residual_paths,
                uncertain_paths=() if residual_paths else (path,),
            )
        ]
    return []


def _fsync_directory(directory: Path) -> None:
    if not _uses_posix_file_security():
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _uses_posix_file_security() -> bool:
    return os.name == "posix"


def _supports_o_nofollow() -> bool:
    return hasattr(os, "O_NOFOLLOW")


def _private_read_flags() -> int:
    return os.O_RDONLY | (os.O_NOFOLLOW if _supports_o_nofollow() else 0)


def _set_private_mode(descriptor: int) -> None:
    if _uses_posix_file_security():
        os.fchmod(descriptor, 0o600)


def _validate_entry(entry: PasswordEntry, usernames: set[str], position: str) -> None:
    if (
        not entry.username
        or not entry.password
        or entry.username != entry.username.strip()
        or entry.password != entry.password.strip()
        or entry.username.startswith("#")
        or entry.role not in {"admin", "user"}
        or entry.username in usernames
        or _contains_unsafe_character(entry.username)
        or _contains_unsafe_character(entry.password)
        or _contains_unsafe_character(entry.role)
        or ":" in entry.username
        or ":" in entry.password
    ):
        raise ValueError(f"password.txt {position} 内容无效")


def _contains_unsafe_character(value: str) -> bool:
    return any(
        character in {"\r", "\n"} or unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"}
        for character in value
    )


def _encode_utf8_or_none(value: str) -> bytes | None:
    try:
        return value.encode("utf-8")
    except UnicodeError:
        return None
