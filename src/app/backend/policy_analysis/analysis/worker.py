"""In-process worker that runs analysis tasks on a thread pool."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.analysis.repository import AnalysisRepository


class AnalysisCallable(Protocol):
    def __call__(self, task_id: int) -> object: ...


class AnalysisWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        runner_factory: Callable[[], AnalysisCallable] | None = None,
        max_workers: int = 1,
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

    @property
    def can_run_tasks(self) -> bool:
        return self._runner_factory is not None

    def start(self) -> None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix="analysis"
                )
        self.submit_next()

    def submit_next(self) -> int | None:
        with self._lock:
            if self._executor is None or self._runner_factory is None:
                return None
            task_id = AnalysisRepository(self._sessions).claim_next(self._now())
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
        repository = AnalysisRepository(self._sessions)
        repository.add_log(task_id, "info", "任务开始执行。")
        try:
            return runner(task_id)
        finally:
            self.submit_next()

    def _discard_future(self, future: Future[object]) -> None:
        with self._lock:
            self._futures.discard(future)


__all__ = ["AnalysisWorker"]
