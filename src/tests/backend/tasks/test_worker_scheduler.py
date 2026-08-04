from __future__ import annotations

import json
from datetime import UTC, datetime

from policy_analysis.sources.models import CollectionRule, PolicyCategory, Schedule, Source
from policy_analysis.tasks.models import CrawlTask, TaskStatus
from policy_analysis.tasks.repository import TaskRepository
from policy_analysis.tasks.scheduler import TaskScheduler
from policy_analysis.tasks.worker import TaskWorker
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def seed_rule(database_sessions: sessionmaker[Session], *, name: str = "规则") -> int:
    with database_sessions.begin() as database:
        category = PolicyCategory(
            code=f"category_{name}",
            name=name,
            description=None,
            is_active=True,
        )
        source = Source(
            code=f"source_{name}",
            name=name,
            organization=name,
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        database.add_all([category, source])
        database.flush()
        rule = CollectionRule(
            source_id=source.id,
            category_id=category.id,
            name=name,
            include_keywords_json='["会议"]',
            exclude_keywords_json="[]",
            history_years=5,
            discovery_config_json=json.dumps(
                {"rss_urls": ["https://www.news.cn/rss.xml"], "channel_urls": []}
            ),
            is_active=True,
        )
        database.add(rule)
        database.flush()
        return rule.id


def test_startup_marks_interrupted_running_task_failed_and_keeps_pending(
    database_sessions: sessionmaker[Session],
) -> None:
    rule_id = seed_rule(database_sessions)
    repository = TaskRepository(database_sessions)
    running = repository.create_task(rule_id, "manual", {"kind": "manual"}, NOW)
    pending = repository.create_task(rule_id, "manual", {"kind": "manual"}, NOW)
    repository.claim(running.id, NOW)

    recovered = repository.recover_interrupted(NOW)

    assert recovered == [running.id]
    assert repository.get(running.id).status == TaskStatus.FAILED.value
    assert repository.get(running.id).error_summary == "服务异常中断"
    assert repository.get(pending.id).status == TaskStatus.PENDING.value


def test_worker_claims_only_one_running_task_per_rule(database_sessions: sessionmaker[Session]) -> None:
    rule_id = seed_rule(database_sessions)
    repository = TaskRepository(database_sessions)
    first = repository.create_task(rule_id, "manual", {"kind": "manual"}, NOW)
    second = repository.create_task(rule_id, "manual", {"kind": "manual"}, NOW)
    started: list[int] = []

    worker = TaskWorker(
        database_sessions,
        runner_factory=lambda: lambda task_id: started.append(task_id),
        max_workers=2,
        now=lambda: NOW,
    )

    worker.start()
    worker.submit_next()
    worker.submit_next()
    worker.shutdown(wait=True)

    assert started == [first.id]
    assert repository.get(first.id).status == TaskStatus.RUNNING.value
    assert repository.get(second.id).status == TaskStatus.PENDING.value


def test_scheduler_creates_one_pending_task_per_schedule_time(
    database_sessions: sessionmaker[Session],
) -> None:
    rule_id = seed_rule(database_sessions)
    with database_sessions.begin() as database:
        database.add(
            Schedule(
                rule_id=rule_id,
                cron_expression="0 9 * * *",
                timezone="Asia/Shanghai",
                is_active=True,
                next_run_at=NOW,
            )
        )

    scheduler = TaskScheduler(database_sessions, now=lambda: NOW)

    created = scheduler.enqueue_due_tasks()
    repeated = scheduler.enqueue_due_tasks()

    assert created == [1]
    assert repeated == []
    with database_sessions() as database:
        tasks = list(database.scalars(select(CrawlTask).order_by(CrawlTask.id)))
        assert len(tasks) == 1
        assert tasks[0].trigger_type == "schedule"
        assert tasks[0].scheduled_for == NOW


def test_scheduler_ignores_inactive_schedules(database_sessions: sessionmaker[Session]) -> None:
    rule_id = seed_rule(database_sessions)
    with database_sessions.begin() as database:
        database.add(
            Schedule(
                rule_id=rule_id,
                cron_expression="0 9 * * *",
                timezone="Asia/Shanghai",
                is_active=False,
                next_run_at=NOW,
            )
        )

    assert TaskScheduler(database_sessions, now=lambda: NOW).enqueue_due_tasks() == []
