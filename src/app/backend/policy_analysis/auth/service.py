"""Synchronize login users from the password-file credential source."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from policy_analysis.auth.models import SessionRecord, User
from policy_analysis.auth.password_file import (
    PasswordEntry,
    PasswordFileError,
    PasswordFileLock,
    PasswordFileOperationError,
    _assert_private_regular_stat,
    _private_read_flags,
    parse_password_text,
    replace_password_file,
    replace_password_file_contents,
)
from policy_analysis.auth.repository import UserRepository
from policy_analysis.core.database import session_scope
from policy_analysis.core.errors import APIError


class PasswordSyncError(RuntimeError):
    """Safe domain error for database failures during credential synchronization."""


class UserSyncService:
    """Apply password-file changes atomically to the local user database."""

    def __init__(
        self,
        password_file: Path,
        sessions: sessionmaker[Session],
        password_hasher: PasswordHasher,
        file_lock: PasswordFileLock | None = None,
    ) -> None:
        self._password_file = password_file
        self._sessions = sessions
        self._password_hasher = password_hasher
        self._last_fingerprint: _PasswordFileFingerprint | None = None
        self.file_lock = file_lock or PasswordFileLock(password_file)
        self._last_usernames: set[str] | None = None

    def sync_if_changed(self) -> bool:
        """Synchronize after a new file mtime; return whether database work was performed."""
        with self.file_lock:
            return self._sync_if_changed_locked()

    def _sync_if_changed_locked(self) -> bool:
        contents, fingerprint = self._read_stable_snapshot()
        if self._last_fingerprint == fingerprint:
            return False

        text = _decode_utf8_or_none(contents)
        if text is None:
            raise PasswordFileError("密码文件编码无效")
        try:
            entries = parse_password_text(text)
        except ValueError:
            raise PasswordFileError("密码文件内容无效") from None
        if not self._synchronize_transaction(entries):
            raise PasswordSyncError("密码同步数据库操作失败") from None
        self._last_fingerprint = fingerprint
        self._last_usernames = {entry.username for entry in entries}
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
                descriptor = os.open(self._password_file, _private_read_flags())
                try:
                    opened_status = os.fstat(descriptor)
                    _assert_private_regular_stat(opened_status)
                    opened = _PasswordFileFingerprint.from_stat(opened_status)
                    if before != opened:
                        continue
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
        raise PasswordFileError("password.txt 文件读取不稳定")

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

            password_needs_update, credential_changed = self._password_state(user, entry.password)
            role_changed = user.role != entry.role
            reactivated = (
                not user.is_active
                and self._last_usernames is not None
                and entry.username not in self._last_usernames
            )
            if password_needs_update:
                user.password_hash = self._password_hasher.hash(entry.password)
            if credential_changed:
                repository.revoke_sessions(user.id)
            if role_changed:
                user.role = entry.role
            if reactivated:
                user.is_active = True
            if password_needs_update or role_changed or reactivated:
                user.password_synced_at = synchronized_at

        for user in repository.list_users():
            if user.username not in entries_by_username:
                user.is_active = False
                repository.revoke_sessions(user.id)

    def record_managed_snapshot(self, entries: list[PasswordEntry]) -> None:
        """Record a file version already committed by the administration service."""
        _contents, fingerprint = self._read_stable_snapshot()
        self._last_fingerprint = fingerprint
        self._last_usernames = {entry.username for entry in entries}

    def _password_state(self, user: User, supplied_password: str) -> tuple[bool, bool]:
        try:
            self._password_hasher.verify(user.password_hash, supplied_password)
            return self._password_hasher.check_needs_rehash(user.password_hash), False
        except (InvalidHashError, VerificationError):
            return True, True


@dataclass(frozen=True, slots=True)
class PublicUser:
    id: int
    username: str
    role: str
    page_permissions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "page_permissions": list(self.page_permissions),
        }


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    csrf_token: str
    user: PublicUser


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    id: int
    user: PublicUser
    csrf_token_hash: str


@dataclass(slots=True)
class _LoginFailureWindow:
    failures: deque[float]
    last_activity: float


class LoginRateLimiter:
    """Bound failed logins by peer address and canonical account identifier."""

    def __init__(
        self,
        *,
        attempts: int,
        window_seconds: int,
        max_active_keys: int,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._attempts = attempts
        self._window_seconds = window_seconds
        self._max_active_keys = max_active_keys
        self._monotonic = monotonic or time.monotonic
        self._failures: OrderedDict[tuple[str, str], _LoginFailureWindow] = OrderedDict()
        self._state_lock = Lock()
        self._key_locks = tuple(Lock() for _ in range(64))

    @contextmanager
    def guard(self, key: tuple[str, str]) -> Iterator[None]:
        """Serialize a complete login attempt for the same logical rate key."""
        lock = self._key_locks[hash(key) % len(self._key_locks)]
        with lock:
            yield

    def ensure_allowed(self, key: tuple[str, str]) -> None:
        with self._state_lock:
            now = self._monotonic()
            self._remove_expired_from_oldest(now)
            window = self._failures.get(key)
            if window is None:
                if len(self._failures) >= self._max_active_keys:
                    raise _login_rate_limited()
                self._failures[key] = _LoginFailureWindow(deque(), now)
                return
            self._remove_expired_failures(window, now)
            if len(window.failures) >= self._attempts:
                raise _login_rate_limited()

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._state_lock:
            now = self._monotonic()
            self._remove_expired_from_oldest(now)
            window = self._failures.get(key)
            if window is None:
                if len(self._failures) >= self._max_active_keys:
                    raise _login_rate_limited()
                window = _LoginFailureWindow(deque(), now)
                self._failures[key] = window
            self._remove_expired_failures(window, now)
            if len(window.failures) < self._attempts:
                window.failures.append(now)
                window.last_activity = now
                self._failures.move_to_end(key)

    def clear(self, key: tuple[str, str]) -> None:
        with self._state_lock:
            self._failures.pop(key, None)

    def _remove_expired_from_oldest(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._failures:
            oldest_key = next(iter(self._failures))
            oldest = self._failures[oldest_key]
            if oldest.last_activity > cutoff:
                return
            self._failures.popitem(last=False)

    def _remove_expired_failures(self, window: _LoginFailureWindow, now: float) -> None:
        cutoff = now - self._window_seconds
        while window.failures and window.failures[0] <= cutoff:
            window.failures.popleft()


def _login_rate_limited() -> APIError:
    return APIError(
        status_code=429,
        code="LOGIN_RATE_LIMITED",
        message="登录尝试过于频繁，请稍后重试。",
    )


class AuthService:
    """Authenticate credentials and manage opaque server-side sessions."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        user_sync: UserSyncService,
        password_hasher: PasswordHasher,
        session_hours: int,
        secure_cookie: bool,
        login_attempts: int,
        login_window_seconds: int,
        login_max_active_keys: int,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._sessions = sessions
        self._user_sync = user_sync
        self._password_hasher = password_hasher
        self._session_hours = session_hours
        self.secure_cookie = secure_cookie
        self._now = now or (lambda: datetime.now(UTC))
        self._dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))
        self._login_limiter = LoginRateLimiter(
            attempts=login_attempts,
            window_seconds=login_window_seconds,
            max_active_keys=login_max_active_keys,
            monotonic=monotonic,
        )

    @property
    def sessions(self) -> sessionmaker[Session]:
        return self._sessions

    @property
    def user_sync(self) -> UserSyncService:
        return self._user_sync

    @property
    def password_hasher(self) -> PasswordHasher:
        return self._password_hasher

    def login(self, username: str, password: str, client_address: str) -> LoginResult:
        self._sync_users()
        normalized_username = username.strip()
        rate_key = (client_address, _normalize_account_identifier(username))
        with self._login_limiter.guard(rate_key):
            self._login_limiter.ensure_allowed(rate_key)
            return self._create_session(normalized_username, password, rate_key)

    def _create_session(
        self,
        username: str,
        password: str,
        rate_key: tuple[str, str],
    ) -> LoginResult:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = self._aware_now()

        with session_scope(self._sessions) as database:
            user = database.scalar(select(User).where(User.username == username))
            verification_hash = user.password_hash if user is not None else self._dummy_password_hash
            try:
                password_matches = self._password_hasher.verify(verification_hash, password)
            except (InvalidHashError, VerificationError):
                password_matches = False
            if user is None or not user.is_active or not password_matches:
                self._login_limiter.record_failure(rate_key)
                raise _invalid_credentials()

            database.add(
                SessionRecord(
                    user_id=user.id,
                    token_hash=_hash_token(token),
                    csrf_token_hash=_hash_token(csrf_token),
                    expires_at=now + timedelta(hours=self._session_hours),
                    created_at=now,
                    last_seen_at=now,
                )
            )
            public_user = _public_user(user)

        self._login_limiter.clear(rate_key)
        return LoginResult(token=token, csrf_token=csrf_token, user=public_user)

    def _sync_users(self) -> None:
        try:
            self._user_sync.sync_if_changed()
        except (PasswordFileError, PasswordSyncError):
            raise APIError(
                status_code=503,
                code="AUTH_SYNC_FAILED",
                message="登录服务暂时不可用。",
            ) from None

    def authenticate_session(self, token: str | None) -> AuthenticatedSession:
        self._sync_users()
        if not token:
            raise _invalid_session()
        token_hash = _hash_token(token)
        now = self._aware_now()
        with session_scope(self._sessions) as database:
            record = database.scalar(
                select(SessionRecord)
                .options(joinedload(SessionRecord.user).joinedload(User.page_permissions))
                .where(SessionRecord.token_hash == token_hash)
            )
            stored_hash = record.token_hash if record is not None else "0" * 64
            token_matches = hmac.compare_digest(stored_hash, token_hash)
            if record is None or not token_matches or record.expires_at <= now or not record.user.is_active:
                raise _invalid_session()
            record.last_seen_at = now
            return AuthenticatedSession(
                id=record.id,
                user=_public_user(record.user),
                csrf_token_hash=record.csrf_token_hash,
            )

    def verify_csrf(self, session: AuthenticatedSession, csrf_token: str | None) -> None:
        supplied_hash = _hash_token(csrf_token or "")
        if not csrf_token or not hmac.compare_digest(session.csrf_token_hash, supplied_hash):
            raise APIError(
                status_code=403,
                code="CSRF_INVALID",
                message="CSRF 校验失败。",
            )

    def logout(self, session_id: int) -> None:
        with session_scope(self._sessions) as database:
            record = database.get(SessionRecord, session_id)
            if record is not None:
                database.delete(record)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("认证时钟必须返回带时区的时间")
        return value.astimezone(UTC)


def _public_user(user: User) -> PublicUser:
    from policy_analysis.auth.permissions import all_page_codes

    return PublicUser(
        id=user.id,
        username=user.username,
        role=user.role,
        page_permissions=(
            all_page_codes()
            if user.role == "admin"
            else tuple(sorted(permission.page_code for permission in user.page_permissions))
        ),
    )


@dataclass(frozen=True, slots=True)
class ManagedUser:
    id: int
    username: str
    role: str
    is_active: bool
    pages: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "pages": list(self.pages),
        }


class UserAdministrationError(RuntimeError):
    """Safe domain failure for a compensated user administration operation."""

    def __init__(
        self,
        message: str,
        outcome: UserAdministrationOutcome | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome or UserAdministrationOutcome.not_applicable(consistency="uncertain")


@dataclass(frozen=True, slots=True)
class UserAdministrationOutcome:
    """Secret-free recovery facts safe for operational audit logging."""

    target_state: str
    durability_state: str
    rollback: str
    compensation: str
    final_sync: str
    close: str
    consistency: str

    @classmethod
    def not_applicable(cls, *, consistency: str = "reconciled") -> UserAdministrationOutcome:
        return cls(
            target_state="not_applicable",
            durability_state="not_applicable",
            rollback="not_run",
            compensation="not_run",
            final_sync="not_run",
            close="not_run",
            consistency=consistency,
        )

    def to_audit_dict(self) -> dict[str, str]:
        return {
            "target_state": self.target_state,
            "durability_state": self.durability_state,
            "rollback": self.rollback,
            "compensation": self.compensation,
            "final_sync": self.final_sync,
            "close": self.close,
            "consistency": self.consistency,
        }


class UserAdministrationService:
    """Keep password.txt authoritative while applying matching database changes."""

    def __init__(
        self,
        *,
        user_sync: UserSyncService,
        sessions: sessionmaker[Session],
        password_hasher: PasswordHasher,
    ) -> None:
        self._user_sync = user_sync
        self._password_file = user_sync._password_file
        self._sessions = sessions
        self._password_hasher = password_hasher

    def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "username",
        sort_order: str = "asc",
    ) -> tuple[list[ManagedUser], int]:
        with self._user_sync.file_lock:
            self._user_sync._sync_if_changed_locked()
            with self._sessions() as database:
                sort_column = {
                    "id": User.id,
                    "username": User.username,
                    "role": User.role,
                    "is_active": User.is_active,
                }[sort_by]
                ordering = sort_column.desc() if sort_order == "desc" else sort_column.asc()
                total = database.scalar(select(func.count(User.id))) or 0
                users = database.scalars(
                    select(User)
                    .options(joinedload(User.page_permissions))
                    .order_by(ordering, User.username.asc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).unique()
                return [_managed_user(user) for user in users], total

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        pages: set[str],
    ) -> ManagedUser:
        def mutate(entries: list[PasswordEntry], database: Session) -> tuple[list[PasswordEntry], User]:
            if any(entry.username == username for entry in entries):
                raise APIError(status_code=409, code="USER_EXISTS", message="用户名已存在。")
            user = database.scalar(
                select(User).options(joinedload(User.page_permissions)).where(User.username == username)
            )
            if user is None:
                user = User(username=username)
                database.add(user)
            user.password_hash = self._password_hasher.hash(password)
            user.role = role
            user.is_active = True
            user.password_synced_at = datetime.now(UTC)
            database.flush()
            _replace_permissions(user, pages)
            UserRepository(database).revoke_sessions(user.id)
            return [*entries, PasswordEntry(username, password, role)], user

        return self._update(mutate)

    def change_password(self, username: str, password: str) -> ManagedUser:
        def mutate(entries: list[PasswordEntry], database: Session) -> tuple[list[PasswordEntry], User]:
            user = _find_user(database, username)
            found = any(entry.username == username for entry in entries)
            updated = [
                PasswordEntry(entry.username, password, entry.role) if entry.username == username else entry
                for entry in entries
            ]
            if not found:
                raise APIError(status_code=404, code="USER_NOT_FOUND", message="用户不存在。")
            user.password_hash = self._password_hasher.hash(password)
            user.password_synced_at = datetime.now(UTC)
            UserRepository(database).revoke_sessions(user.id)
            return updated, user

        return self._update(mutate)

    def change_role(self, username: str, role: str) -> ManagedUser:
        def mutate(entries: list[PasswordEntry], database: Session) -> tuple[list[PasswordEntry], User]:
            user = _find_user(database, username)
            found = any(entry.username == username for entry in entries)
            updated = [
                PasswordEntry(entry.username, entry.password, role) if entry.username == username else entry
                for entry in entries
            ]
            if not found:
                raise APIError(status_code=404, code="USER_NOT_FOUND", message="用户不存在。")
            _ensure_admin_remains(database, user, next_role=role)
            user.role = role
            user.password_synced_at = datetime.now(UTC)
            return updated, user

        return self._update(mutate)

    def set_active(self, username: str, is_active: bool) -> ManagedUser:
        with self._user_sync.file_lock:
            self._user_sync._sync_if_changed_locked()
            operation_failed = False
            try:
                with session_scope(self._sessions) as database:
                    user = _find_user(database, username)
                    if is_active:
                        _contents, entries = self._read_entries_locked()
                        if not any(entry.username == username for entry in entries):
                            raise APIError(
                                status_code=404,
                                code="USER_NOT_FOUND",
                                message="用户不存在。",
                            )
                    _ensure_admin_remains(database, user, next_active=is_active)
                    user.is_active = is_active
                    if not is_active:
                        UserRepository(database).revoke_sessions(user.id)
                    database.flush()
                    result = _managed_user(user)
            except APIError:
                raise
            except Exception:
                operation_failed = True
            if operation_failed:
                raise UserAdministrationError("用户状态更新失败") from None
            return result

    def set_pages(self, username: str, pages: set[str]) -> ManagedUser:
        with self._user_sync.file_lock:
            self._user_sync._sync_if_changed_locked()
            operation_failed = False
            try:
                with session_scope(self._sessions) as database:
                    user = _find_user(database, username)
                    _replace_permissions(user, pages)
                    database.flush()
                    result = _managed_user(user)
            except APIError:
                raise
            except Exception:
                operation_failed = True
            if operation_failed:
                raise UserAdministrationError("页面授权更新失败") from None
            return result

    def _update(
        self,
        mutate: Callable[[list[PasswordEntry], Session], tuple[list[PasswordEntry], User]],
    ) -> ManagedUser:
        with self._user_sync.file_lock:
            self._user_sync._sync_if_changed_locked()
            original_contents, original_entries = self._read_entries_locked()
            database = self._sessions()
            file_replaced = False
            updated_entries = original_entries
            result: ManagedUser | None = None
            failure: BaseException | None = None
            try:
                updated_entries, user = mutate(original_entries, database)
                database.flush()
                replace_password_file(self._password_file, updated_entries)
                file_replaced = True
                database.commit()
                result = _managed_user(user)
                self._user_sync.record_managed_snapshot(updated_entries)
            except BaseException as error:
                failure = error
            recovery_errors: list[BaseException] = []
            rollback_state = "not_run"
            compensation_state = "not_run"
            final_sync_state = "not_run"
            needs_recovery = failure is not None and (
                file_replaced or _file_contains_attempted_update(failure)
            )
            if failure is not None:
                recovery_errors.append(failure)
                rollback_state = _run_recovery_stage(database.rollback, recovery_errors)
                if needs_recovery:
                    compensation_state = _run_recovery_stage(
                        lambda: self._compensate(original_contents, original_entries),
                        recovery_errors,
                    )
                    final_sync_state = _run_recovery_stage(
                        self._synchronize_final_file,
                        recovery_errors,
                    )
            close_state = _run_recovery_stage(database.close, recovery_errors)
            if failure is None and close_state == "succeeded":
                if result is None:
                    raise AssertionError("用户管理操作未返回结果")
                return result
            outcome = _build_administration_outcome(
                errors=recovery_errors,
                file_replaced=file_replaced,
                operation_succeeded=failure is None,
                rollback=rollback_state,
                compensation=compensation_state,
                final_sync=final_sync_state,
                close=close_state,
            )
            system_failures = _system_failures(recovery_errors)
            if len(system_failures) == 1:
                raise system_failures[0]
            if system_failures:
                raise BaseExceptionGroup("用户管理操作被系统级异常中断", system_failures)
            cleanup_failed = any(
                state == "failed"
                for state in (rollback_state, compensation_state, final_sync_state, close_state)
            )
            if isinstance(failure, APIError) and not cleanup_failed:
                raise failure
            if failure is not None or close_state == "failed":
                raise UserAdministrationError("用户管理操作失败", outcome) from None
            raise AssertionError("用户管理操作未返回结果")

    def _read_entries_locked(self) -> tuple[bytes, list[PasswordEntry]]:
        contents, _fingerprint = self._user_sync._read_stable_snapshot()
        text = _decode_utf8_or_none(contents)
        if text is None:
            raise UserAdministrationError("用户管理操作失败")
        try:
            return contents, parse_password_text(text)
        except ValueError:
            raise UserAdministrationError("用户管理操作失败") from None

    def _compensate(
        self,
        original_contents: bytes,
        original_entries: list[PasswordEntry],
    ) -> None:
        replace_password_file_contents(self._password_file, original_contents)
        self._user_sync.record_managed_snapshot(original_entries)

    def _synchronize_final_file(self) -> None:
        self._user_sync._last_fingerprint = None
        self._user_sync._sync_if_changed_locked()


def _find_user(database: Session, username: str) -> User:
    user = database.scalar(
        select(User).options(joinedload(User.page_permissions)).where(User.username == username)
    )
    if user is None:
        raise APIError(status_code=404, code="USER_NOT_FOUND", message="用户不存在。")
    return user


def _ensure_admin_remains(
    database: Session,
    user: User,
    *,
    next_role: str | None = None,
    next_active: bool | None = None,
) -> None:
    resulting_role = user.role if next_role is None else next_role
    resulting_active = user.is_active if next_active is None else next_active
    removes_active_admin = (
        user.role == "admin" and user.is_active and not (resulting_role == "admin" and resulting_active)
    )
    if not removes_active_admin:
        return
    active_admins = database.scalar(
        select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
    )
    if active_admins == 1:
        raise APIError(
            status_code=409,
            code="LAST_ACTIVE_ADMIN",
            message="必须保留至少一个启用的管理员。",
        )


def _run_recovery_stage(
    operation: Callable[[], object],
    errors: list[BaseException],
) -> str:
    try:
        operation()
    except BaseException as error:
        errors.append(error)
        return "failed"
    return "succeeded"


def _system_failures(errors: list[BaseException]) -> list[BaseException]:
    system_errors: list[BaseException] = []

    def collect(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                collect(nested)
        elif not isinstance(error, Exception):
            system_errors.append(error)

    for error in errors:
        collect(error)
    return system_errors


def _build_administration_outcome(
    *,
    errors: list[BaseException],
    file_replaced: bool,
    operation_succeeded: bool,
    rollback: str,
    compensation: str,
    final_sync: str,
    close: str,
) -> UserAdministrationOutcome:
    operation_errors = [item for error in errors for item in _operation_errors(error)]
    target_states = {error.target_state for error in operation_errors}
    durability_states = {error.durability_state for error in operation_errors}
    if len(target_states) == 1:
        target_state = target_states.pop()
    elif target_states:
        target_state = "unknown"
    elif operation_succeeded and file_replaced:
        target_state = "persisted"
    elif compensation == "succeeded":
        target_state = "restored"
    elif file_replaced:
        target_state = "unknown"
    else:
        target_state = "not_applicable"
    if len(durability_states) == 1:
        durability_state = durability_states.pop()
    elif durability_states:
        durability_state = "unknown"
    elif target_state in {"persisted", "restored", "not_replaced"}:
        durability_state = "durable"
    elif target_state == "not_applicable":
        durability_state = "not_applicable"
    else:
        durability_state = "unknown"
    consistency = (
        "reconciled"
        if operation_succeeded
        or final_sync == "succeeded"
        or (rollback == "succeeded" and compensation == "succeeded")
        else "uncertain"
    )
    return UserAdministrationOutcome(
        target_state=target_state,
        durability_state=durability_state,
        rollback=rollback,
        compensation=compensation,
        final_sync=final_sync,
        close=close,
        consistency=consistency,
    )


def _replace_permissions(user: User, pages: set[str]) -> None:
    from policy_analysis.auth.models import PagePermission

    user.page_permissions[:] = [PagePermission(page_code=page) for page in sorted(pages)]


def _managed_user(user: User) -> ManagedUser:
    from policy_analysis.auth.permissions import all_page_codes

    pages = (
        all_page_codes()
        if user.role == "admin"
        else tuple(sorted(permission.page_code for permission in user.page_permissions))
    )
    return ManagedUser(user.id, user.username, user.role, user.is_active, pages)


def _operation_states(error: BaseException) -> set[str]:
    states: set[str] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, PasswordFileOperationError):
            states.add(current.target_state)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return states


def _operation_errors(error: BaseException) -> tuple[PasswordFileOperationError, ...]:
    result: list[PasswordFileOperationError] = []
    pending = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, PasswordFileOperationError):
            result.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return tuple(result)


def _file_contains_attempted_update(error: BaseException) -> bool:
    states = _operation_states(error)
    return bool(states & {"persisted", "replaced_pending_durability"})


def _file_restored_previous(error: BaseException) -> bool:
    states = _operation_states(error)
    return bool(states & {"restored", "restored_pending_durability", "not_replaced"})


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_account_identifier(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def _invalid_credentials() -> APIError:
    return APIError(
        status_code=401,
        code="AUTH_INVALID",
        message="用户名或密码错误。",
    )


def _invalid_session() -> APIError:
    return APIError(
        status_code=401,
        code="SESSION_INVALID",
        message="会话无效或已过期。",
    )


def _decode_utf8_or_none(contents: bytes) -> str | None:
    try:
        return contents.decode("utf-8")
    except UnicodeError:
        return None


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
