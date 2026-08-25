"""Persistence for analysis tasks, word results, relations and logs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.analysis.models import (
    COMPARISON_VALUE,
    WORD_VALUE,
    AnalysisComparisonReport,
    AnalysisTask,
    AnalysisTaskLog,
    AnalysisTaskPolicy,
    AnalysisTaskStatus,
    AnalysisWordRelation,
    AnalysisWordResult,
)
from policy_analysis.analysis.state import transition
from policy_analysis.policies.models import Policy


class AnalysisRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AnalysisRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, task_id: int) -> AnalysisTask | None:
        with self._sessions() as session:
            return session.get(AnalysisTask, task_id)

    def list_tasks(self, *, offset: int = 0, limit: int = 50) -> tuple[list[AnalysisTask], int]:
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(AnalysisTask)) or 0
            tasks = list(
                session.scalars(
                    select(AnalysisTask).order_by(AnalysisTask.id.desc()).offset(offset).limit(limit)
                )
            )
            session.expunge_all()
            return tasks, total

    def create_task(
        self,
        policy_ids: Sequence[int],
        now: datetime,
        *,
        requested_by: int | None = None,
        task_type: str = WORD_VALUE,
    ) -> AnalysisTask:
        _utc(now)
        unique_ids = list(dict.fromkeys(policy_ids))
        if not unique_ids:
            raise AnalysisRepositoryError("ANALYSIS_POLICY_IDS_EMPTY", "至少选择一篇政策。")
        if task_type not in {WORD_VALUE, COMPARISON_VALUE}:
            raise AnalysisRepositoryError("ANALYSIS_TASK_TYPE_INVALID", "分析任务类型无效。")
        if task_type == COMPARISON_VALUE and len(unique_ids) < 2:
            raise AnalysisRepositoryError("COMPARISON_REQUIRES_TWO_POLICIES", "政策比对至少需要两篇政策。")
        snapshot = json.dumps(
            {"policy_ids": unique_ids},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._sessions.begin() as session:
            existing = set(session.scalars(select(Policy.id).where(Policy.id.in_(unique_ids))))
            missing = set(unique_ids) - existing
            if missing:
                raise AnalysisRepositoryError("POLICY_NOT_FOUND", "部分政策不存在。")
            task = AnalysisTask(
                task_type=task_type,
                status=AnalysisTaskStatus.PENDING.value,
                requested_by=requested_by,
                policy_count=len(unique_ids),
                request_snapshot_json=snapshot,
            )
            session.add(task)
            session.flush()
            task_id = task.id
            session.add_all(
                [AnalysisTaskPolicy(task_id=task_id, policy_id=policy_id) for policy_id in unique_ids]
            )
            session.flush()
        stored = self.get(task_id)
        if stored is None:  # pragma: no cover - defensive database invariant
            raise AnalysisRepositoryError("ANALYSIS_TASK_CREATE_FAILED", "分析任务创建失败。")
        return stored

    def claim_next(self, now: datetime) -> int | None:
        now = _utc(now)
        with self._sessions.begin() as session:
            candidate = session.scalar(
                select(AnalysisTask)
                .where(AnalysisTask.status == AnalysisTaskStatus.PENDING.value)
                .order_by(AnalysisTask.id)
            )
            if candidate is None:
                return None
            claimed = session.execute(
                update(AnalysisTask)
                .where(
                    AnalysisTask.id == candidate.id,
                    AnalysisTask.status == AnalysisTaskStatus.PENDING.value,
                )
                .values(status=AnalysisTaskStatus.RUNNING.value, started_at=now)
            ).rowcount
            return candidate.id if claimed == 1 else None

    def recover_interrupted(self, now: datetime) -> list[int]:
        now = _utc(now)
        with self._sessions.begin() as session:
            ids = list(
                session.scalars(
                    select(AnalysisTask.id)
                    .where(AnalysisTask.status == AnalysisTaskStatus.RUNNING.value)
                    .order_by(AnalysisTask.id)
                )
            )
            if not ids:
                return []
            session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id.in_(ids))
                .values(status=AnalysisTaskStatus.FAILED.value, finished_at=now, error_summary="服务异常中断")
            )
            return ids

    def load_policies(self, task_id: int) -> list[tuple[int, str]]:
        """Return [(policy_id, content_text)] for the task, in insertion order."""
        with self._sessions() as session:
            policy_ids = list(
                session.scalars(
                    select(AnalysisTaskPolicy.policy_id)
                    .where(AnalysisTaskPolicy.task_id == task_id)
                    .order_by(AnalysisTaskPolicy.id)
                )
            )
            if not policy_ids:
                return []
            policies = list(
                session.scalars(select(Policy).where(Policy.id.in_(policy_ids)).order_by(Policy.id))
            )
            session.expunge_all()
            by_id = {policy.id: policy for policy in policies}
            return [
                (policy_id, by_id[policy_id].content_text) for policy_id in policy_ids if policy_id in by_id
            ]

    def load_policy_details(self, task_id: int) -> list[dict[str, object]]:
        """Return policy metadata and content in the task selection order."""
        with self._sessions() as session:
            policy_ids = list(
                session.scalars(
                    select(AnalysisTaskPolicy.policy_id)
                    .where(AnalysisTaskPolicy.task_id == task_id)
                    .order_by(AnalysisTaskPolicy.id)
                )
            )
            policies = list(session.scalars(select(Policy).where(Policy.id.in_(policy_ids))))
            by_id = {policy.id: policy for policy in policies}
            return [
                {
                    "id": policy_id,
                    "title": by_id[policy_id].title,
                    "publisher": by_id[policy_id].publisher,
                    "published_at": by_id[policy_id].published_at,
                    "content_text": by_id[policy_id].content_text,
                }
                for policy_id in policy_ids
                if policy_id in by_id
            ]

    def store_comparison_report(self, task_id: int, report: Mapping[str, object], now: datetime) -> None:
        _utc(now)
        payload = json.dumps(
            report,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        )
        with self._sessions.begin() as session:
            session.add(AnalysisComparisonReport(task_id=task_id, report_json=payload))

    def get_comparison_report(self, task_id: int) -> dict[str, object] | None:
        with self._sessions() as session:
            report = session.scalar(
                select(AnalysisComparisonReport).where(AnalysisComparisonReport.task_id == task_id)
            )
            return json.loads(report.report_json) if report is not None else None

    def store_results(
        self,
        task_id: int,
        results: Sequence[tuple[int, dict[str, tuple[int, float]]]],
        now: datetime,
    ) -> None:
        _utc(now)
        objects: list[AnalysisWordResult] = []
        for policy_id, word_map in results:
            for word, (freq, tfidf) in word_map.items():
                objects.append(
                    AnalysisWordResult(
                        task_id=task_id,
                        policy_id=policy_id,
                        word=word,
                        frequency=freq,
                        tfidf=tfidf,
                    )
                )
        if not objects:
            return
        with self._sessions.begin() as session:
            session.add_all(objects)
            session.flush()

    def store_relations(
        self,
        task_id: int,
        relations: Sequence[tuple[str, str, int]],
        now: datetime,
    ) -> None:
        _utc(now)
        if not relations:
            return
        with self._sessions.begin() as session:
            session.add_all(
                [
                    AnalysisWordRelation(task_id=task_id, word1=word1, word2=word2, co_count=co_count)
                    for word1, word2, co_count in relations
                ]
            )
            session.flush()

    def finish(
        self,
        task_id: int,
        status: AnalysisTaskStatus,
        now: datetime,
        *,
        error_summary: str | None = None,
    ) -> AnalysisTaskStatus:
        now = _utc(now)
        with self._sessions.begin() as session:
            task = session.get(AnalysisTask, task_id)
            if task is None:
                raise AnalysisRepositoryError("ANALYSIS_TASK_NOT_FOUND", "分析任务不存在。")
            current = AnalysisTaskStatus(task.status)
            if current is not AnalysisTaskStatus.RUNNING:
                return current
            transition(current, status)
            task.status = status.value
            task.finished_at = now
            task.error_summary = error_summary
            return status

    def add_log(
        self,
        task_id: int,
        level: str,
        message: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        if level not in {"debug", "info", "warning", "error"}:
            raise AnalysisRepositoryError("ANALYSIS_LOG_LEVEL_INVALID", "日志级别无效。")
        payload = json.dumps(
            dict(context or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._sessions.begin() as session:
            session.add(AnalysisTaskLog(task_id=task_id, level=level, message=message, context_json=payload))

    def list_logs(
        self, task_id: int, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[AnalysisTaskLog], int]:
        with self._sessions() as session:
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(AnalysisTaskLog)
                    .where(AnalysisTaskLog.task_id == task_id)
                )
                or 0
            )
            logs = list(
                session.scalars(
                    select(AnalysisTaskLog)
                    .where(AnalysisTaskLog.task_id == task_id)
                    .order_by(AnalysisTaskLog.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            session.expunge_all()
            return logs, total

    def list_words(
        self,
        task_id: int,
        *,
        top: int = 50,
        sort_by: str = "frequency",
        policy_id: int | None = None,
    ) -> list[dict[str, object]]:
        with self._sessions() as session:
            stmt = (
                select(
                    AnalysisWordResult.word,
                    func.sum(AnalysisWordResult.frequency).label("frequency"),
                    func.avg(AnalysisWordResult.tfidf).label("tfidf"),
                    func.count(AnalysisWordResult.id).label("doc_count"),
                )
                .where(AnalysisWordResult.task_id == task_id)
                .group_by(AnalysisWordResult.word)
            )
            if policy_id is not None:
                stmt = stmt.where(AnalysisWordResult.policy_id == policy_id)
            if sort_by == "tfidf":
                stmt = stmt.order_by(func.avg(AnalysisWordResult.tfidf).desc())
            else:
                stmt = stmt.order_by(func.sum(AnalysisWordResult.frequency).desc())
            stmt = stmt.limit(top)
            rows = session.execute(stmt).all()
            session.expunge_all()
            return [
                {
                    "word": row.word,
                    "frequency": int(row.frequency or 0),
                    "tfidf": float(row.tfidf or 0.0),
                    "doc_count": int(row.doc_count or 0),
                }
                for row in rows
            ]

    def list_relations(self, task_id: int, *, top: int = 50) -> tuple[list[AnalysisWordRelation], list[str]]:
        with self._sessions() as session:
            node_stmt = (
                select(AnalysisWordResult.word)
                .where(AnalysisWordResult.task_id == task_id)
                .group_by(AnalysisWordResult.word)
                .order_by(func.sum(AnalysisWordResult.frequency).desc())
                .limit(top)
            )
            nodes = [row[0] for row in session.execute(node_stmt).all()]
            if not nodes:
                session.expunge_all()
                return [], []
            node_set = set(nodes)
            relations = list(
                session.scalars(
                    select(AnalysisWordRelation)
                    .where(
                        AnalysisWordRelation.task_id == task_id,
                        AnalysisWordRelation.word1.in_(node_set),
                        AnalysisWordRelation.word2.in_(node_set),
                    )
                    .order_by(AnalysisWordRelation.co_count.desc())
                )
            )
            session.expunge_all()
            return relations, nodes


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("分析任务时钟必须返回 aware datetime")
    return value.astimezone(UTC)


__all__ = ["AnalysisRepository", "AnalysisRepositoryError"]
