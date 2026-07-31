from datetime import UTC, datetime

from policy_analysis.auth.models import User
from policy_analysis.core.database import build_engine, create_schema, session_factory
from sqlalchemy import inspect, text


def test_sqlite_enables_foreign_keys_wal_and_auth_tables(tmp_path) -> None:
    engine = build_engine(tmp_path / "app.sqlite3")
    create_schema(engine)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"

    assert {"users", "page_permissions", "sessions"}.issubset(inspect(engine).get_table_names())


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
