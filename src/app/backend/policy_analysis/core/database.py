from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_engine(database_path: Path) -> Engine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    from policy_analysis.auth import models as auth_models
    from policy_analysis.policies import models as policy_models
    from policy_analysis.sources import models as source_models
    from policy_analysis.tasks import models as task_models

    del auth_models, policy_models, source_models, task_models
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
