"""Persistence operations needed by password-file synchronization."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from policy_analysis.auth.models import User


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
