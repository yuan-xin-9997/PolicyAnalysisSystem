"""Parsing and safe replacement of the password credential source file."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Literal

Role = Literal["admin", "user"]

_HEADER = "# 格式: username:password:role  (role 取值: admin | user)"
_ROLE_HELP = "# admin 默认拥有所有页面权限；user 的页面权限由管理员配置。"


class PasswordFileError(RuntimeError):
    """A credential-file error whose message never includes credential values."""


class PasswordFileOperationError(PasswordFileError):
    """Safe, structured operational state for controlled credential-file recovery."""

    def __init__(
        self,
        message: str,
        target_state: str,
        backup_path: Path | None,
        residual_paths: tuple[Path, ...],
    ):
        super().__init__(message)
        self.target_state = target_state
        self.backup_path = backup_path
        self.residual_paths = residual_paths


class _UpdateState(Enum):
    NOT_REPLACED = auto()
    REPLACED_PENDING_DURABILITY = auto()
    RESTORED = auto()
    PERSISTED = auto()


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
    rendered = render_password_text(entries).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    temporary_path: Path | None = None
    state = _UpdateState.NOT_REPLACED
    existed = os.path.lexists(path)

    try:
        if existed:
            _assert_regular_private(path)
            backup_path = _write_private_copy(path.parent, f".{path.name}.backup.", _read_private_file(path))
            _fsync_directory(path.parent)
        temporary_path = _write_private_copy(path.parent, f".{path.name}.tmp.", rendered)
        os.replace(temporary_path, path)
        temporary_path = None
        state = _UpdateState.REPLACED_PENDING_DURABILITY
        _fsync_directory(path.parent)
        state = _UpdateState.PERSISTED
    except Exception as original_error:
        if state is _UpdateState.REPLACED_PENDING_DURABILITY:
            recovery_error = _restore_previous(path, backup_path, existed)
            if recovery_error is not None:
                # Keep backup_path intact: it is the only durable old version on failed recovery.
                raise ExceptionGroup("密码文件更新和恢复失败", [original_error, recovery_error]) from None
            state = _UpdateState.RESTORED
            cleanup_error = _remove_backup_after_recovery(backup_path)
            if cleanup_error is not None:
                raise ExceptionGroup("密码文件更新失败且清理失败", [original_error, cleanup_error]) from None
        raise
    finally:
        cleanup_errors = _cleanup_errors(temporary_path, state, backup_path)
        if state is _UpdateState.PERSISTED:
            _remove_backup_after_success(backup_path)
        elif state is _UpdateState.NOT_REPLACED:
            cleanup_errors.extend(_cleanup_errors(backup_path, state, backup_path))
        if cleanup_errors:
            active_error = sys.exception()
            if active_error is not None:
                raise ExceptionGroup("密码文件操作与清理失败", [active_error, *cleanup_errors]) from None
            raise cleanup_errors[0]


def _restore_previous(path: Path, backup_path: Path | None, existed: bool) -> Exception | None:
    try:
        if existed:
            if backup_path is None:
                return PasswordFileError("密码文件恢复失败")
            recovery_path = _write_private_copy(
                path.parent, f".{path.name}.restore.", _read_private_file(backup_path)
            )
            try:
                os.replace(recovery_path, path)
                _fsync_directory(path.parent)
            finally:
                cleanup_errors = _cleanup_errors(recovery_path, _UpdateState.RESTORED, backup_path)
                if cleanup_errors:
                    raise cleanup_errors[0]
        else:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
    except Exception as error:
        return error
    return None


def _remove_backup_after_success(backup_path: Path | None) -> None:
    if backup_path is None:
        return
    try:
        backup_path.unlink()
        _fsync_directory(backup_path.parent)
    except OSError as error:
        raise PasswordFileError("密码文件更新已持久化，但清理失败") from error


def _remove_backup_after_recovery(backup_path: Path | None) -> OSError | None:
    if backup_path is None:
        return None
    try:
        backup_path.unlink()
        _fsync_directory(backup_path.parent)
    except OSError as error:
        # The update itself already failed; retaining a private backup is safer than hiding it.
        return error
    return None


def _read_private_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _assert_private_regular_stat(os.fstat(descriptor))
        return _read_all(descriptor)
    finally:
        os.close(descriptor)


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
    except Exception as original_error:
        if owns_descriptor:
            os.close(descriptor)
        cleanup_errors = _cleanup_errors(result, _UpdateState.NOT_REPLACED, None)
        if cleanup_errors:
            raise ExceptionGroup("密码文件写入与清理失败", [original_error, *cleanup_errors]) from None
        raise
    return result


def _assert_regular_private(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as error:
        raise PasswordFileError("凭据文件不可用") from error
    _assert_private_regular_stat(status)


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
        residual_paths = (path,) if path.exists() else ()
        return [
            PasswordFileOperationError(
                "密码文件清理失败",
                target_state=state.name.lower(),
                backup_path=backup_path,
                residual_paths=residual_paths,
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
        character in {"\r", "\n"} or unicodedata.category(character) in {"Cc", "Zl", "Zp"}
        for character in value
    )
