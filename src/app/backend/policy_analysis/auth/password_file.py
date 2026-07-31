"""Parsing and safe replacement of the password credential source file."""

import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Role = Literal["admin", "user"]

_HEADER = "# 格式: username:password:role  (role 取值: admin | user)"
_ROLE_HELP = "# admin 默认拥有所有页面权限；user 的页面权限由管理员配置。"


@dataclass(frozen=True, slots=True)
class PasswordEntry:
    username: str
    password: str
    role: Role


def parse_password_text(text: str) -> list[PasswordEntry]:
    """Parse credential entries, rejecting malformed input before any database work."""
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
        username, password, role = parts
        _validate_entry(PasswordEntry(username, password, role), usernames, f"第 {line_number} 行")
        usernames.add(username)
        entries.append(PasswordEntry(username=username, password=password, role=role))

    return entries


def render_password_text(entries: list[PasswordEntry]) -> str:
    """Render only the supplied credential entries; callers must not include stale entries."""
    usernames: set[str] = set()
    for entry_number, entry in enumerate(entries, start=1):
        _validate_entry(entry, usernames, f"条目 {entry_number}")
        usernames.add(entry.username)

    lines = [_HEADER, _ROLE_HELP]
    lines.extend(f"{entry.username}:{entry.password}:{entry.role}" for entry in entries)
    return "\n".join(lines) + "\n"


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
    return any(character in {"\r", "\n"} or unicodedata.category(character) == "Cc" for character in value)


def replace_password_file(path: Path, entries: list[PasswordEntry]) -> None:
    """Durably atomically replace ``path`` with a mode-0600 password file."""
    rendered = render_password_text(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent, text=True)
    temporary_path = Path(temporary_name)
    backup_path: Path | None = None
    replaced = False

    try:
        if path.exists():
            backup_path = _write_private_copy(path.parent, f".{path.name}.backup.", path.read_bytes())
            _fsync_directory(path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)
        replaced = True
        _fsync_directory(path.parent)
    except Exception:
        if replaced:
            try:
                if backup_path is not None:
                    os.replace(backup_path, path)
                    backup_path = None
                else:
                    path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
            except OSError:
                pass
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)


def _write_private_copy(directory: Path, prefix: str, contents: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, dir=directory)
    backup_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
