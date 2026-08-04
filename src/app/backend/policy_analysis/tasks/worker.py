from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.tasks.repository import TaskRepository


class TaskCallable(Protocol):
    def __call__(self, task_id: int) -> object: ...


class TaskWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        runner_factory: Callable[[], TaskCallable] | None = None,
        max_workers: int = 2,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._runner_factory = runner_factory
        self._max_workers = max_workers
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[object]] = set()

    @property
    def is_started(self) -> bool:
        return self._executor is not None

    def start(self) -> None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="tasks")
        self.submit_next()

    def submit_next(self) -> int | None:
        with self._lock:
            if self._executor is None:
                return None
            if self._runner_factory is None:
                return None
            task_id = TaskRepository(self._sessions).claim_next(self._now())
            if task_id is None:
                return None
            future = self._executor.submit(self._run, task_id)
            self._futures.add(future)
            future.add_done_callback(self._discard_future)
            return task_id

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=False)

    def _run(self, task_id: int) -> object:
        runner = self._runner_factory()
        repository = TaskRepository(self._sessions)
        repository.add_log(task_id, "info", "任务开始执行。")
        try:
            result = runner(task_id)
            status = getattr(getattr(result, "status", None), "value", None)
            repository.add_log(task_id, "info", "任务执行结束。", {"status": status or "unknown"})
            return result
        finally:
            self.submit_next()

    def _discard_future(self, future: Future[object]) -> None:
        with self._lock:
            self._futures.discard(future)


__all__ = ["TaskWorker"]
