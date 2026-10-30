"""Create first-run credentials without overwriting existing account migrations."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from policy_analysis.auth.password_file import parse_password_text


def initialize_admin(path: Path, environment: Mapping[str, str] | None = None) -> bool:
    """Create a private password file once; return whether it was created.

    The random password is only written to the private file, never to logs.
    Existing files remain authoritative, including legacy plaintext migrations.
    """
    path = Path(path)
    if path.exists():
        return False
    environment = os.environ if environment is None else environment
    password = environment.get("POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD", secrets.token_urlsafe(24))
    if "\n" in password or "\r" in password:
        raise ValueError("初始管理员密码不能为空或包含冒号、换行")
    parse_password_text(f"admin:{password}:admin\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"admin:{password}:admin\n")
    return True
