from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.sources.models import CollectionRule
from policy_analysis.tasks.repository import TaskRepository


class TaskScheduler:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._now = now or (lambda: datetime.now(UTC))
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._wakeup: Callable[[], None] | None = None

    @property
    def is_started(self) -> bool:
        return self._scheduler.running

    def start(self) -> None:
        if not self._scheduler.running:
            self.sync_jobs()
            self._scheduler.start(paused=False)

    def shutdown(self, *, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def enqueue_due_tasks(self) -> list[int]:
        repository = TaskRepository(self._sessions)
        created_ids: list[int] = []
        now = self._now()
        for rule in repository.due_scheduled_rules(now):
            task = repository.create_scheduled_task_once(
                rule.id,
                rule.next_run_at,
                now,
                next_run_at=_next_fire(rule, now),
            )
            if task is not None:
                created_ids.append(task.id)
                self._wake_worker()
        return created_ids

    def sync_jobs(self) -> None:
        for job in self._scheduler.get_jobs():
            self._scheduler.remove_job(job.id)
        repository = TaskRepository(self._sessions)
        for rule in repository.scheduled_rules():
            trigger = _build_trigger(rule)
            if trigger is None:
                continue
            self._scheduler.add_job(
                self.enqueue_rule,
                trigger,
                id=f"rule:{rule.id}",
                args=[rule.id],
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )

    def enqueue_rule(self, rule_id: int) -> int | None:
        repository = TaskRepository(self._sessions)
        rule = repository.get_scheduled_rule(rule_id)
        if rule is None:
            return None
        now = self._now()
        task = repository.create_scheduled_task_once(
            rule_id,
            now,
            now,
            next_run_at=_next_fire(rule, now),
        )
        if task is not None:
            self._wake_worker()
        return None if task is None else task.id

    def set_worker_wakeup(self, wakeup: Callable[[], None] | None) -> None:
        self._wakeup = wakeup

    def _wake_worker(self) -> None:
        wakeup = getattr(self, "_wakeup", None)
        if wakeup is not None:
            wakeup()


def _build_trigger(rule: CollectionRule) -> CronTrigger | None:
    if not rule.cron_expression:
        return None
    try:
        return CronTrigger.from_crontab(rule.cron_expression, timezone=rule.schedule_timezone)
    except ValueError:
        return None


def _next_fire(rule: CollectionRule, now: datetime) -> datetime | None:
    trigger = _build_trigger(rule)
    if trigger is None:
        return None
    fire_time = trigger.get_next_fire_time(None, now)
    return None if fire_time is None else fire_time.astimezone(UTC)


__all__ = ["TaskScheduler"]
