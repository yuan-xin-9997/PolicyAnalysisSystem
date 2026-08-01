from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.auth.models import User
from policy_analysis.core.database import build_engine, create_schema, session_factory
from sqlalchemy import Engine, delete, inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

AUTH_TABLES = {"users", "page_permissions", "sessions"}
COLLECTION_TABLES = {
    "policy_categories",
    "sources",
    "collection_rules",
    "seed_urls",
    "schedules",
    "policies",
    "policy_revisions",
    "crawl_tasks",
    "crawl_task_items",
    "crawl_task_logs",
}


def _new_engine(tmp_path: Path, name: str = "collection.sqlite3") -> Engine:
    engine = build_engine(tmp_path / name)
    create_schema(engine)
    return engine


def _schema_signature(engine: Engine, table_name: str) -> dict[str, Any]:
    inspector = inspect(engine)
    return {
        "columns": {
            column["name"]: {
                "nullable": column["nullable"],
                "type": str(column["type"]),
                "default": column["default"],
            }
            for column in inspector.get_columns(table_name)
        },
        "unique": {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table_name)
        },
        "foreign_keys": {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
                foreign_key["options"].get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        },
        "indexes": {
            (index["name"], tuple(index["column_names"]), index["unique"])
            for index in inspector.get_indexes(table_name)
        },
        "checks": {
            (constraint["name"], " ".join(constraint["sqltext"].split()))
            for constraint in inspector.get_check_constraints(table_name)
        },
    }


def _add_source_graph(session: Session):
    from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source

    category = PolicyCategory(code="politburo", name="中央政治局会议")
    source = Source(
        code="xinhua",
        name="新华网",
        organization="新华社",
        base_url="https://www.news.cn",
        adapter_type="xinhua",
        allowed_domains_json='["news.cn", "xinhuanet.com"]',
    )
    rule = CollectionRule(
        source=source,
        category=category,
        name="新华社中央政治局会议",
        include_keywords_json='["中共中央政治局召开会议"]',
        exclude_keywords_json='["视频"]',
        history_years=5,
        discovery_config_json='{"rss_urls": ["https://www.news.cn/rss/politics.xml"]}',
    )
    session.add(rule)
    session.flush()
    return category, source, rule


def _add_user(session: Session, username: str = "collector") -> User:
    user = User(username=username, password_hash="hash")
    session.add(user)
    session.flush()
    return user


def _add_policy(session: Session, source_id: int, category_id: int):
    from policy_analysis.policies.models import Policy

    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    policy = Policy(
        source_id=source_id,
        category_id=category_id,
        title="中共中央政治局召开会议",
        canonical_url="https://www.news.cn/politics/20260801/example.htm",
        publisher="新华社",
        published_at=now,
        content_text="新华社北京电，中共中央政治局召开会议。",
        content_hash="sha256-current",
        webfetch_artifact_id="artifact-current",
        first_crawled_at=now,
        last_crawled_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(policy)
    session.flush()
    return policy


def _add_task(session: Session, rule_id: int, requested_by: int | None = None):
    from policy_analysis.tasks.models import CrawlTask

    task = CrawlTask(
        rule_id=rule_id,
        trigger_type="manual",
        status="pending",
        requested_by=requested_by,
        request_snapshot_json='{"history_years": 5}',
    )
    session.add(task)
    session.flush()
    return task


def test_create_schema_has_auth_and_all_collection_tables_with_required_nullability(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) >= AUTH_TABLES | COLLECTION_TABLES
    expected_columns = {
        "policy_categories": (
            {"id", "code", "name", "description", "is_active"},
            {"description"},
        ),
        "sources": (
            {
                "id",
                "code",
                "name",
                "organization",
                "base_url",
                "adapter_type",
                "allowed_domains_json",
                "is_active",
            },
            set(),
        ),
        "collection_rules": (
            {
                "id",
                "source_id",
                "category_id",
                "name",
                "include_keywords_json",
                "exclude_keywords_json",
                "history_years",
                "discovery_config_json",
                "is_active",
                "created_at",
                "updated_at",
            },
            set(),
        ),
        "seed_urls": (
            {
                "id",
                "rule_id",
                "url",
                "expected_title",
                "expected_published_date",
                "is_verified",
                "created_at",
            },
            set(),
        ),
        "schedules": (
            {"id", "rule_id", "cron_expression", "timezone", "is_active", "next_run_at", "last_run_at"},
            {"next_run_at", "last_run_at"},
        ),
        "policies": (
            {
                "id",
                "source_id",
                "category_id",
                "title",
                "canonical_url",
                "publisher",
                "published_at",
                "content_text",
                "content_hash",
                "webfetch_artifact_id",
                "first_crawled_at",
                "last_crawled_at",
                "created_at",
                "updated_at",
            },
            set(),
        ),
        "policy_revisions": (
            {
                "id",
                "policy_id",
                "content_text",
                "content_hash",
                "webfetch_artifact_id",
                "replaced_at",
                "task_item_id",
            },
            {"task_item_id"},
        ),
        "crawl_tasks": (
            {
                "id",
                "rule_id",
                "trigger_type",
                "status",
                "requested_by",
                "scheduled_for",
                "started_at",
                "finished_at",
                "cancel_requested_at",
                "request_snapshot_json",
                "discovered_count",
                "success_count",
                "duplicate_count",
                "filtered_count",
                "failed_count",
                "error_summary",
            },
            {
                "requested_by",
                "scheduled_for",
                "started_at",
                "finished_at",
                "cancel_requested_at",
                "error_summary",
            },
        ),
        "crawl_task_items": (
            {
                "id",
                "task_id",
                "candidate_url",
                "normalized_url",
                "status",
                "policy_id",
                "attempt_count",
                "reason_code",
                "reason_message",
                "started_at",
                "finished_at",
            },
            {"normalized_url", "policy_id", "reason_code", "reason_message", "started_at", "finished_at"},
        ),
        "crawl_task_logs": (
            {"id", "task_id", "level", "message", "context_json", "created_at"},
            set(),
        ),
    }
    for table_name, (expected_names, nullable_columns) in expected_columns.items():
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(columns) == expected_names
        assert {name for name, column in columns.items() if column["nullable"]} == nullable_columns

    task_item_columns = {column["name"] for column in inspector.get_columns("crawl_task_items")}
    assert {"reason_code", "reason_message"} <= task_item_columns
    task_columns = {column["name"]: column for column in inspector.get_columns("crawl_tasks")}
    assert task_columns["request_snapshot_json"]["nullable"] is False


def test_collection_schema_has_named_unique_constraints(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    inspector = inspect(engine)

    expected = {
        "policy_categories": {("uq_policy_categories_code", ("code",))},
        "sources": {("uq_sources_code", ("code",))},
        "seed_urls": {("uq_seed_urls_rule_url", ("rule_id", "url"))},
        "policies": {("uq_policies_source_canonical_url", ("source_id", "canonical_url"))},
    }
    for table_name, constraints in expected.items():
        actual = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert constraints <= actual


def test_policies_have_required_named_indexes_in_column_order(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    indexes = {
        (index["name"], tuple(index["column_names"])) for index in inspect(engine).get_indexes("policies")
    }

    assert indexes >= {
        ("ix_policies_source_content_hash", ("source_id", "content_hash")),
        ("ix_policies_published_at", ("published_at",)),
        ("ix_policies_last_crawled_at", ("last_crawled_at",)),
        ("ix_policies_publisher", ("publisher",)),
        ("ix_policies_category_id", ("category_id",)),
    }


@pytest.mark.parametrize(
    ("mutation", "expected_constraint"),
    [
        (lambda values: values.update(history_years=0), "ck_collection_rules_history_years"),
        (lambda values: values.update(history_years=21), "ck_collection_rules_history_years"),
    ],
)
def test_collection_rule_rejects_history_years_outside_supported_range(
    tmp_path,
    mutation: Callable[[dict[str, Any]], None],
    expected_constraint: str,
) -> None:
    from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source

    engine = _new_engine(tmp_path)
    values: dict[str, Any] = {
        "source": Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
        ),
        "category": PolicyCategory(code="politburo", name="中央政治局会议"),
        "name": "规则",
        "include_keywords_json": "[]",
        "exclude_keywords_json": "[]",
        "history_years": 5,
        "discovery_config_json": "{}",
    }
    mutation(values)

    with session_factory(engine)() as session:
        session.add(CollectionRule(**values))
        with pytest.raises(IntegrityError, match=expected_constraint):
            session.commit()


@pytest.mark.parametrize(
    ("target", "invalid_value", "expected_constraint"),
    [
        ("trigger_type", "automatic", "ck_crawl_tasks_trigger_type"),
        ("task_status", "done", "ck_crawl_tasks_status"),
        ("item_status", "pending", "ck_crawl_task_items_status"),
        ("request_snapshot", "", "ck_crawl_tasks_request_snapshot_nonempty"),
        ("request_snapshot", "   ", "ck_crawl_tasks_request_snapshot_nonempty"),
        ("discovered_count", -1, "ck_crawl_tasks_discovered_count_nonnegative"),
        ("success_count", -1, "ck_crawl_tasks_success_count_nonnegative"),
        ("duplicate_count", -1, "ck_crawl_tasks_duplicate_count_nonnegative"),
        ("filtered_count", -1, "ck_crawl_tasks_filtered_count_nonnegative"),
        ("failed_count", -1, "ck_crawl_tasks_failed_count_nonnegative"),
        ("attempt_count", -1, "ck_crawl_task_items_attempt_count_nonnegative"),
    ],
)
def test_task_tables_reject_invalid_states_empty_snapshot_and_negative_counts(
    tmp_path,
    target: str,
    invalid_value: Any,
    expected_constraint: str,
) -> None:
    from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        _, _, rule = _add_source_graph(session)
        task_values = {
            "rule_id": rule.id,
            "trigger_type": "manual",
            "status": "pending",
            "request_snapshot_json": '{"history_years": 5}',
            "discovered_count": 0,
            "success_count": 0,
            "duplicate_count": 0,
            "filtered_count": 0,
            "failed_count": 0,
        }
        if target == "trigger_type":
            task_values["trigger_type"] = invalid_value
        elif target == "task_status":
            task_values["status"] = invalid_value
        elif target == "request_snapshot":
            task_values["request_snapshot_json"] = invalid_value
        elif target in {
            "discovered_count",
            "success_count",
            "duplicate_count",
            "filtered_count",
            "failed_count",
        }:
            task_values[target] = invalid_value
        task = CrawlTask(**task_values)
        if target in {"item_status", "attempt_count"}:
            session.add(task)
            session.flush()
            item_values = {
                "task_id": task.id,
                "candidate_url": "https://www.news.cn/example.htm",
                "status": "filtered",
                "attempt_count": 0,
            }
            item_values["status" if target == "item_status" else target] = invalid_value
            session.add(CrawlTaskItem(**item_values))
        else:
            session.add(task)

        with pytest.raises(IntegrityError, match=expected_constraint):
            session.commit()


@pytest.mark.parametrize(
    ("model_name", "field_name"),
    [
        ("Source", "allowed_domains_json"),
        ("CollectionRule", "include_keywords_json"),
        ("CollectionRule", "exclude_keywords_json"),
        ("CollectionRule", "discovery_config_json"),
        ("CrawlTaskLog", "context_json"),
    ],
)
def test_json_text_columns_reject_blank_text(tmp_path, model_name: str, field_name: str) -> None:
    from policy_analysis.tasks.models import CrawlTaskLog

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        category, source, rule = _add_source_graph(session)
        if model_name == "Source":
            source.allowed_domains_json = " "
        elif model_name == "CollectionRule":
            setattr(rule, field_name, " ")
        else:
            task = _add_task(session, rule.id)
            session.add(CrawlTaskLog(task_id=task.id, level="INFO", message="started", context_json=" "))

        with pytest.raises(IntegrityError):
            session.commit()

        assert category.code == "politburo"


def test_rule_deletion_cascades_seed_urls_and_schedules(tmp_path) -> None:
    from policy_analysis.sources.models import Schedule, SeedUrl

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        _, _, rule = _add_source_graph(session)
        seed = SeedUrl(
            rule_id=rule.id,
            url="https://www.news.cn/example.htm",
            expected_title="中共中央政治局召开会议",
            expected_published_date=date(2026, 8, 1),
            is_verified=True,
        )
        schedule = Schedule(rule_id=rule.id, cron_expression="0 2 * * *")
        session.add_all([seed, schedule])
        session.commit()
        seed_id, schedule_id, rule_id = seed.id, schedule.id, rule.id

        session.execute(delete(type(rule)).where(type(rule).id == rule_id))
        session.commit()
        session.expunge_all()

        assert session.get(type(seed), seed_id) is None
        assert session.get(type(schedule), schedule_id) is None


def test_task_deletion_cascades_items_and_logs_but_preserves_revision(tmp_path) -> None:
    from policy_analysis.policies.models import PolicyRevision
    from policy_analysis.tasks.models import CrawlTaskItem, CrawlTaskLog

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        category, source, rule = _add_source_graph(session)
        policy = _add_policy(session, source.id, category.id)
        task = _add_task(session, rule.id)
        item = CrawlTaskItem(
            task_id=task.id,
            candidate_url=policy.canonical_url,
            normalized_url=policy.canonical_url,
            status="updated",
            policy_id=policy.id,
        )
        session.add(item)
        session.flush()
        revision = PolicyRevision(
            policy_id=policy.id,
            content_text="旧正文",
            content_hash="sha256-old",
            webfetch_artifact_id="artifact-old",
            task_item_id=item.id,
        )
        log = CrawlTaskLog(task_id=task.id, level="INFO", message="updated", context_json="{}")
        session.add_all([revision, log])
        session.commit()
        task_id, item_id, log_id, revision_id = task.id, item.id, log.id, revision.id

        session.execute(delete(type(task)).where(type(task).id == task_id))
        session.commit()
        session.expire_all()

        assert session.get(type(item), item_id) is None
        assert session.get(type(log), log_id) is None
        preserved_revision = session.get(type(revision), revision_id)
        assert preserved_revision is not None
        assert preserved_revision.task_item_id is None


def test_policy_deletion_cascades_revisions_and_nulls_task_item_policy(tmp_path) -> None:
    from policy_analysis.policies.models import PolicyRevision
    from policy_analysis.tasks.models import CrawlTaskItem

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        category, source, rule = _add_source_graph(session)
        policy = _add_policy(session, source.id, category.id)
        task = _add_task(session, rule.id)
        item = CrawlTaskItem(
            task_id=task.id,
            candidate_url=policy.canonical_url,
            status="stored",
            policy_id=policy.id,
        )
        revision = PolicyRevision(
            policy_id=policy.id,
            content_text="旧正文",
            content_hash="sha256-old",
            webfetch_artifact_id="artifact-old",
        )
        session.add_all([item, revision])
        session.commit()
        policy_id, item_id, revision_id = policy.id, item.id, revision.id

        session.execute(delete(type(policy)).where(type(policy).id == policy_id))
        session.commit()
        session.expire_all()

        assert session.get(type(revision), revision_id) is None
        preserved_item = session.get(type(item), item_id)
        assert preserved_item is not None
        assert preserved_item.policy_id is None


def test_item_deletion_nulls_revision_task_item_reference(tmp_path) -> None:
    from policy_analysis.policies.models import PolicyRevision
    from policy_analysis.tasks.models import CrawlTaskItem

    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        category, source, rule = _add_source_graph(session)
        policy = _add_policy(session, source.id, category.id)
        task = _add_task(session, rule.id)
        item = CrawlTaskItem(task_id=task.id, candidate_url=policy.canonical_url, status="updated")
        session.add(item)
        session.flush()
        revision = PolicyRevision(
            policy_id=policy.id,
            content_text="旧正文",
            content_hash="sha256-old",
            webfetch_artifact_id="artifact-old",
            task_item_id=item.id,
        )
        session.add(revision)
        session.commit()
        item_id, revision_id = item.id, revision.id

        session.execute(delete(type(item)).where(type(item).id == item_id))
        session.commit()
        session.expire_all()

        preserved_revision = session.get(type(revision), revision_id)
        assert preserved_revision is not None
        assert preserved_revision.task_item_id is None


def test_user_deletion_nulls_task_requester(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        _, _, rule = _add_source_graph(session)
        user = _add_user(session)
        task = _add_task(session, rule.id, user.id)
        session.commit()
        user_id, task_id = user.id, task.id

        session.execute(delete(User).where(User.id == user_id))
        session.commit()
        session.expire_all()

        preserved_task = session.get(type(task), task_id)
        assert preserved_task is not None
        assert preserved_task.requested_by is None


def test_sources_and_categories_cannot_be_deleted_while_historical_data_references_them(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        category, source, _rule = _add_source_graph(session)
        session.commit()
        category_id, source_id = category.id, source.id

        with pytest.raises(IntegrityError):
            session.execute(delete(type(source)).where(type(source).id == source_id))
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(delete(type(category)).where(type(category).id == category_id))
            session.commit()


def test_collection_datetime_round_trips_as_aware_utc_and_rejects_naive_values(tmp_path) -> None:
    from policy_analysis.sources.models import PolicyCategory

    engine = _new_engine(tmp_path)
    timestamp = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    with session_factory(engine)() as session:
        category = PolicyCategory(code="time-aware", name="时区", is_active=True)
        session.add(category)
        session.flush()
        _, _, rule = _add_source_graph(session)
        rule.created_at = timestamp
        rule.updated_at = timestamp
        session.commit()
        rule_id = rule.id
        session.expunge_all()

        loaded_rule = session.get(type(rule), rule_id)
        assert loaded_rule is not None
        assert loaded_rule.created_at == timestamp
        assert loaded_rule.created_at.tzinfo == UTC

        loaded_rule.updated_at = datetime(2026, 8, 1, 8, 0)
        with pytest.raises(StatementError, match="时间值必须包含时区信息"):
            session.commit()


def test_sqlite_foreign_keys_are_enabled_for_collection_schema(tmp_path) -> None:
    engine = _new_engine(tmp_path)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_alembic_collection_schema_matches_orm_and_round_trips(tmp_path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[4]
    orm_engine = _new_engine(tmp_path, "orm.sqlite3")
    migration_path = tmp_path / "migrated.sqlite3"
    for environment_name in list(os.environ):
        if environment_name.startswith("POLICY_ANALYSIS_"):
            monkeypatch.delenv(environment_name)
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(migration_path))
    config = Config(str(project_root / "alembic.ini"))
    migration_engine = downgraded_engine = upgraded_again_engine = None
    try:
        command.upgrade(config, "head")
        migration_engine = build_engine(migration_path)
        assert set(inspect(migration_engine).get_table_names()) >= AUTH_TABLES | COLLECTION_TABLES
        with migration_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        for table_name in COLLECTION_TABLES:
            assert _schema_signature(migration_engine, table_name) == _schema_signature(
                orm_engine, table_name
            )

        migration_engine.dispose()
        migration_engine = None
        command.downgrade(config, "0001")
        downgraded_engine = build_engine(migration_path)
        downgraded_tables = set(inspect(downgraded_engine).get_table_names())
        assert downgraded_tables >= AUTH_TABLES
        assert COLLECTION_TABLES.isdisjoint(downgraded_tables)
        with downgraded_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
        downgraded_engine.dispose()
        downgraded_engine = None

        command.upgrade(config, "head")
        upgraded_again_engine = build_engine(migration_path)
        assert set(inspect(upgraded_again_engine).get_table_names()) >= AUTH_TABLES | COLLECTION_TABLES
        with upgraded_again_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
    finally:
        orm_engine.dispose()
        if migration_engine is not None:
            migration_engine.dispose()
        if downgraded_engine is not None:
            downgraded_engine.dispose()
        if upgraded_again_engine is not None:
            upgraded_again_engine.dispose()


def test_task_status_enums_expose_persisted_string_values() -> None:
    from policy_analysis.tasks.models import TaskItemStatus, TaskStatus

    assert [status.value for status in TaskStatus] == [
        "pending",
        "running",
        "succeeded",
        "partially_succeeded",
        "failed",
        "cancelled",
    ]
    assert [status.value for status in TaskItemStatus] == [
        "stored",
        "updated",
        "duplicate",
        "filtered",
        "failed",
    ]


def test_required_json_and_counter_defaults_apply_to_direct_sql_inserts(tmp_path) -> None:
    engine = _new_engine(tmp_path)
    with session_factory(engine)() as session:
        _, _, rule = _add_source_graph(session)
        session.commit()
        rule_id = rule.id

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO crawl_tasks (rule_id, trigger_type, status, request_snapshot_json) "
                "VALUES (:rule_id, 'manual', 'pending', '{}')"
            ),
            {"rule_id": rule_id},
        )
        row = connection.execute(
            text(
                "SELECT discovered_count, success_count, duplicate_count, filtered_count, failed_count "
                "FROM crawl_tasks"
            )
        ).one()

    assert tuple(row) == (0, 0, 0, 0, 0)
