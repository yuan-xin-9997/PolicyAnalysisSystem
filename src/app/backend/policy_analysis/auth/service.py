"""Synchronize login users from the password-file credential source."""

from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.auth.models import User
from policy_analysis.auth.password_file import PasswordEntry, parse_password_text
from policy_analysis.auth.repository import UserRepository
from policy_analysis.core.database import session_scope


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
        self._last_mtime_ns: int | None = None

    def sync_if_changed(self) -> bool:
        """Synchronize after a new file mtime; return whether database work was performed."""
        mtime_ns = self._password_file.stat().st_mtime_ns
        if self._last_mtime_ns == mtime_ns:
            return False

        entries = parse_password_text(self._password_file.read_text(encoding="utf-8"))
        with session_scope(self._sessions) as session:
            self._synchronize(UserRepository(session), entries)
        self._last_mtime_ns = mtime_ns
        return True

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

            password_changed = self._password_changed(user, entry.password)
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

    def _password_changed(self, user: User, supplied_password: str) -> bool:
        try:
            return not self._password_hasher.verify(user.password_hash, supplied_password)
        except (InvalidHashError, VerificationError):
            return True
