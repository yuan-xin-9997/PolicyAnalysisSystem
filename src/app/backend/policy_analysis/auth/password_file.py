"""Parsing and safe replacement of the password credential source file."""

import os
import tempfile
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
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            raise ValueError(f"password.txt 第 {line_number} 行格式无效")
        username, password, role = (part.strip() for part in parts)
        if not username or not password or role not in {"admin", "user"} or username in usernames:
            raise ValueError(f"password.txt 第 {line_number} 行内容无效")
        usernames.add(username)
        entries.append(PasswordEntry(username=username, password=password, role=role))

    return entries


def render_password_text(entries: list[PasswordEntry]) -> str:
    """Render only the supplied credential entries; callers must not include stale entries."""
    lines = [_HEADER, _ROLE_HELP]
    lines.extend(f"{entry.username}:{entry.password}:{entry.role}" for entry in entries)
    return "\n".join(lines) + "\n"


def replace_password_file(path: Path, entries: list[PasswordEntry]) -> None:
    """Durably atomically replace ``path`` with a mode-0600 password file."""
    rendered = render_password_text(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)
