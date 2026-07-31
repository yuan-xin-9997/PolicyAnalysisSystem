"""Synchronize login users from the password-file credential source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.auth.models import User
from policy_analysis.auth.password_file import (
    PasswordEntry,
    _assert_private_regular_stat,
    parse_password_text,
)
from policy_analysis.auth.repository import UserRepository
from policy_analysis.core.database import session_scope


class PasswordSyncError(RuntimeError):
    """Safe domain error for database failures during credential synchronization."""


class UserSyncService:
    """Apply password-file changes atomically to the local user database."""

    def __init__(
        self,
        password_file: Path,
        sessions: sessionmaker[Session],
        password_hasher: PasswordHasher,
    ) -> None:
        self._password_file = password_file
        self._sessions = sessions
        self._password_hasher = password_hasher
        self._last_fingerprint: _PasswordFileFingerprint | None = None

    def sync_if_changed(self) -> bool:
        """Synchronize after a new file mtime; return whether database work was performed."""
        contents, fingerprint = self._read_stable_snapshot()
        if self._last_fingerprint == fingerprint:
            return False

        entries = parse_password_text(contents.decode("utf-8"))
        if not self._synchronize_transaction(entries):
            raise PasswordSyncError("密码同步数据库操作失败") from None
        self._last_fingerprint = fingerprint
        return True

    def _synchronize_transaction(self, entries: list[PasswordEntry]) -> bool:
        """Return a safe failure signal after the SQLAlchemy exception has unwound."""
        try:
            with session_scope(self._sessions) as session:
                self._synchronize(UserRepository(session), entries)
        except SQLAlchemyError:
            return False
        return True

    def _read_stable_snapshot(self) -> tuple[bytes, _PasswordFileFingerprint]:
        for _ in range(2):
            try:
                before_status = self._password_file.lstat()
                _assert_private_regular_stat(before_status)
                before = _PasswordFileFingerprint.from_stat(before_status)
                descriptor = os.open(self._password_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    opened_status = os.fstat(descriptor)
                    _assert_private_regular_stat(opened_status)
                    opened = _PasswordFileFingerprint.from_stat(opened_status)
                    chunks: list[bytes] = []
                    while chunk := os.read(descriptor, 64 * 1024):
                        chunks.append(chunk)
                    after_read = _PasswordFileFingerprint.from_stat(os.fstat(descriptor))
                finally:
                    os.close(descriptor)
                current_status = self._password_file.lstat()
                _assert_private_regular_stat(current_status)
                current = _PasswordFileFingerprint.from_stat(current_status)
            except OSError:
                continue
            if before == opened == after_read == current:
                return b"".join(chunks), current
        raise RuntimeError("password.txt 文件读取不稳定")

    def _synchronize(self, repository: UserRepository, entries: list[PasswordEntry]) -> None:
        synchronized_at = datetime.now(UTC)
        entries_by_username = {entry.username: entry for entry in entries}

        for entry in entries:
            user = repository.get_by_username(entry.username)
            if user is None:
                repository.add(
                    User(
                        username=entry.username,
                        password_hash=self._password_hasher.hash(entry.password),
                        role=entry.role,
                        is_active=True,
                        password_synced_at=synchronized_at,
                    )
                )
                continue

            password_changed = self._password_needs_rehash(user, entry.password)
            role_changed = user.role != entry.role
            reactivated = not user.is_active
            if password_changed:
                user.password_hash = self._password_hasher.hash(entry.password)
            if role_changed:
                user.role = entry.role
            if reactivated:
                user.is_active = True
            if password_changed or role_changed or reactivated:
                user.password_synced_at = synchronized_at

        for user in repository.list_users():
            if user.username not in entries_by_username:
                user.is_active = False

    def _password_needs_rehash(self, user: User, supplied_password: str) -> bool:
        try:
            self._password_hasher.verify(user.password_hash, supplied_password)
            return self._password_hasher.check_needs_rehash(user.password_hash)
        except (InvalidHashError, VerificationError):
            return True


@dataclass(frozen=True, slots=True)
class _PasswordFileFingerprint:
    device: int
    inode: int
    mtime_ns: int
    size: int

    @classmethod
    def from_stat(cls, stat_result: os.stat_result) -> _PasswordFileFingerprint:
        return cls(
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
        )
