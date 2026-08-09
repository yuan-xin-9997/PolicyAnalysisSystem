"""Analysis service: task creation, status, and result aggregation."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.analysis.models import AnalysisTask
from policy_analysis.analysis.repository import AnalysisRepository, AnalysisRepositoryError
from policy_analysis.analysis.schemas import (
    AnalysisTaskLogItem,
    AnalysisTaskLogPage,
    AnalysisTaskPage,
    AnalysisTaskSummary,
    CreateAnalysisTaskResponse,
    WordFrequencyItem,
    WordFrequencyResult,
    WordRelationItem,
    WordRelationResult,
)
from policy_analysis.core.errors import APIError


class AnalysisService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def repository(self) -> AnalysisRepository:
        return AnalysisRepository(self._sessions)

    def create_task(self, policy_ids: list[int], *, requested_by: int | None) -> CreateAnalysisTaskResponse:
        try:
            task = self.repository.create_task(policy_ids, self._now(), requested_by=requested_by)
        except AnalysisRepositoryError as error:
            if error.code == "POLICY_NOT_FOUND":
                raise APIError(status_code=404, code="POLICY_NOT_FOUND", message="部分政策不存在。") from None
            raise APIError(status_code=400, code=error.code, message=str(error)) from None
        return CreateAnalysisTaskResponse(task_id=task.id, status=task.status)

    def get_task(self, task_id: int) -> AnalysisTaskSummary:
        task = self.repository.get(task_id)
        if task is None:
            raise APIError(status_code=404, code="ANALYSIS_TASK_NOT_FOUND", message="分析任务不存在。")
        return _task_to_summary(task)

    def list_tasks(self, *, page: int, page_size: int) -> AnalysisTaskPage:
        offset = (page - 1) * page_size
        tasks, total = self.repository.list_tasks(offset=offset, limit=page_size)
        return AnalysisTaskPage(
            items=[_task_to_summary(task) for task in tasks],
            total=total,
            page=page,
            page_size=page_size,
        )

    def list_words(
        self, task_id: int, *, top: int, sort_by: str, policy_id: int | None
    ) -> WordFrequencyResult:
        self._require_task(task_id)
        rows = self.repository.list_words(task_id, top=top, sort_by=sort_by, policy_id=policy_id)
        items = [
            WordFrequencyItem(
                word=row["word"],
                frequency=row["frequency"],
                tfidf=row["tfidf"],
                doc_count=row["doc_count"],
            )
            for row in rows
        ]
        return WordFrequencyResult(items=items, total=len(items))

    def list_relations(self, task_id: int, *, top: int) -> WordRelationResult:
        self._require_task(task_id)
        relations, nodes = self.repository.list_relations(task_id, top=top)
        return WordRelationResult(
            items=[WordRelationItem(word1=r.word1, word2=r.word2, co_count=r.co_count) for r in relations],
            nodes=nodes,
        )

    def list_logs(self, task_id: int, *, page: int, page_size: int) -> AnalysisTaskLogPage:
        self._require_task(task_id)
        offset = (page - 1) * page_size
        logs, total = self.repository.list_logs(task_id, offset=offset, limit=page_size)
        return AnalysisTaskLogPage(
            items=[
                AnalysisTaskLogItem(
                    id=log.id,
                    level=log.level,
                    message=log.message,
                    context=json.loads(log.context_json),
                    created_at=log.created_at,
                )
                for log in logs
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def _require_task(self, task_id: int) -> None:
        if self.repository.get(task_id) is None:
            raise APIError(status_code=404, code="ANALYSIS_TASK_NOT_FOUND", message="分析任务不存在。")


def _task_to_summary(task: AnalysisTask) -> AnalysisTaskSummary:
    return AnalysisTaskSummary(
        id=task.id,
        task_type=task.task_type,
        status=task.status,
        policy_count=task.policy_count,
        requested_by=task.requested_by,
        started_at=task.started_at,
        finished_at=task.finished_at,
        error_summary=task.error_summary,
        created_at=task.created_at,
    )


__all__ = ["AnalysisService"]
