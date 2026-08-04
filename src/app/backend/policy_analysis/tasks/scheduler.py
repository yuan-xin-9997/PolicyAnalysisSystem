from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

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
        for schedule in repository.due_schedules(now):
            task = repository.create_scheduled_task_once(schedule.id, schedule.next_run_at, now)
            if task is not None:
                created_ids.append(task.id)
                self._wake_worker()
        return created_ids

    def sync_jobs(self) -> None:
        for job in self._scheduler.get_jobs():
            self._scheduler.remove_job(job.id)
        repository = TaskRepository(self._sessions)
        for schedule in repository.enabled_schedules():
            self._scheduler.add_job(
                self.enqueue_schedule,
                CronTrigger.from_crontab(schedule.cron_expression, timezone=schedule.timezone),
                id=f"schedule:{schedule.id}",
                args=[schedule.id],
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )

    def enqueue_schedule(self, schedule_id: int) -> int | None:
        task = TaskRepository(self._sessions).create_scheduled_task_once(
            schedule_id,
            self._now(),
            self._now(),
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


__all__ = ["TaskScheduler"]
