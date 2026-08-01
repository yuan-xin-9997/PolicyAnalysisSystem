"""Persistence operations needed by password-file synchronization."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from policy_analysis.auth.models import SessionRecord, User


class UserRepository:
    """Small session-bound repository for synchronizing users from password.txt."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username(self, username: str) -> User | None:
        return self._session.scalar(select(User).where(User.username == username))

    def list_users(self) -> list[User]:
        return list(self._session.scalars(select(User)))

    def add(self, user: User) -> None:
        self._session.add(user)

    def revoke_sessions(self, user_id: int) -> None:
        self._session.execute(delete(SessionRecord).where(SessionRecord.user_id == user_id))
