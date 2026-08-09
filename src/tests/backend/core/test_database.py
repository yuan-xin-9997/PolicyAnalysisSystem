from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.auth.models import User
from policy_analysis.core.database import build_engine, create_schema, session_factory, session_scope
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import StatementError


def test_sqlite_enables_foreign_keys_wal_and_auth_tables(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000

    assert {"users", "page_permissions", "sessions"}.issubset(inspect(engine).get_table_names())


def test_sqlite_engine_hides_bound_parameters(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")

    assert engine.hide_parameters is True


def test_auth_schema_enforces_unique_keys_and_cascading_foreign_keys(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    inspector = inspect(engine)

    assert {column["name"] for column in inspector.get_columns("users")} == {
        "id",
        "username",
        "password_hash",
        "role",
        "is_active",
        "created_at",
        "updated_at",
        "password_synced_at",
    }
    assert {column["name"] for column in inspector.get_columns("page_permissions")} == {
        "user_id",
        "page_code",
    }
    assert {column["name"] for column in inspector.get_columns("sessions")} == {
        "id",
        "user_id",
        "token_hash",
        "csrf_token_hash",
        "expires_at",
        "created_at",
        "last_seen_at",
    }
    user_unique_columns = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("users")
    }
    assert user_unique_columns >= {
        ("username",),
    }
    assert {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("page_permissions")
    } >= {("user_id", "page_code")}
    for table_name in ("page_permissions", "sessions"):
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["constrained_columns"] == ["user_id"]
        assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"


def test_auth_timestamps_round_trip_with_utc_semantics(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    timestamp = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    with session_factory(engine)() as session:
        user = User(
            username="timezone-test",
            password_hash="hash",
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(user)
        session.commit()
        user_id = user.id
        session.expunge_all()

        loaded_user = session.get(User, user_id)

    assert loaded_user is not None
    assert loaded_user.created_at == timestamp
    assert loaded_user.updated_at == timestamp


def test_auth_timestamps_persist_as_iso8601_text_with_utc_offset(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    timestamp = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    with session_factory(engine)() as session:
        session.add(
            User(
                username="raw-timezone-test",
                password_hash="hash",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.commit()

    with engine.connect() as connection:
        raw_created_at = connection.execute(
            text("SELECT created_at FROM users WHERE username = 'raw-timezone-test'")
        ).scalar_one()

    assert raw_created_at == timestamp.isoformat()
    assert raw_created_at.endswith("+00:00")


def test_auth_timestamps_reject_naive_values(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    naive_timestamp = datetime(2026, 7, 31, 12, 0)

    with session_factory(engine)() as session:
        session.add(
            User(
                username="naive-timezone-test",
                password_hash="hash",
                created_at=naive_timestamp,
                updated_at=naive_timestamp,
            )
        )

        with pytest.raises(StatementError, match="时间值必须包含时区信息"):
            session.commit()


def test_session_scope_commits_successful_work(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    factory = session_factory(engine)

    with session_scope(factory) as session:
        session.add(User(username="committed-user", password_hash="hash"))

    with factory() as verification_session:
        saved_user = verification_session.scalar(select(User).where(User.username == "committed-user"))

    assert saved_user is not None


def test_session_scope_rolls_back_work_when_block_raises(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)
    factory = session_factory(engine)

    with pytest.raises(RuntimeError, match="abort transaction"), session_scope(factory) as session:
        session.add(User(username="rolled-back-user", password_hash="hash"))
        raise RuntimeError("abort transaction")

    with factory() as verification_session:
        saved_user = verification_session.scalar(select(User).where(User.username == "rolled-back-user"))

    assert saved_user is None


def test_alembic_upgrade_uses_environment_overridden_temporary_database(tmp_path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[4]
    database_path = tmp_path / "migrated.sqlite3"
    default_database_path = project_root / "src/data/app.sqlite3"
    default_database_existed = default_database_path.exists()
    default_database_metadata = (
        (default_database_path.stat().st_size, default_database_path.stat().st_mtime_ns)
        if default_database_existed
        else None
    )
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(database_path))

    command.upgrade(Config(str(project_root / "alembic.ini")), "head")

    assert database_path.is_file()
    if default_database_existed:
        assert default_database_path.exists()
        assert (default_database_path.stat().st_size, default_database_path.stat().st_mtime_ns) == (
            default_database_metadata
        )
    else:
        assert not default_database_path.exists()
    engine = build_engine(database_path)
    inspector = inspect(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004"
    assert {"users", "page_permissions", "sessions"}.issubset(inspector.get_table_names())
    assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("users")} >= {
        ("username",),
    }
    assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("page_permissions")} >= {
        ("user_id", "page_code")
    }
    assert inspector.get_foreign_keys("sessions")[0]["options"]["ondelete"] == "CASCADE"
