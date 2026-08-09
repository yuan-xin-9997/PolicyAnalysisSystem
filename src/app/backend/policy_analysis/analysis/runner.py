"""Executes a claimed analysis task end-to-end (tokenize -> TF-IDF -> co-occurrence)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.analysis import engine
from policy_analysis.analysis.models import AnalysisTaskStatus
from policy_analysis.analysis.repository import AnalysisRepository, AnalysisRepositoryError

_logger = logging.getLogger(__name__)


class AnalysisRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        min_word_length: int = 2,
        top_words_default: int = 50,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._min_word_length = min_word_length
        self._top_words_default = top_words_default
        self._now = now or (lambda: datetime.now(UTC))

    def run_claimed(self, task_id: int) -> AnalysisTaskStatus:
        repository = AnalysisRepository(self._sessions)
        try:
            policies = repository.load_policies(task_id)
            if not policies:
                repository.add_log(task_id, "warning", "任务未关联任何政策。")
                return repository.finish(
                    task_id, AnalysisTaskStatus.FAILED, self._now(), error_summary="任务未关联任何政策。"
                )

            repository.add_log(task_id, "info", "开始词频分析。", {"policy_count": len(policies)})

            doc_words = [
                engine.analyze_text(content, min_word_length=self._min_word_length)
                for _policy_id, content in policies
            ]
            policy_ids = [policy_id for policy_id, _ in policies]

            doc_word_maps = engine.compute_tfidf(doc_words)
            repository.store_results(task_id, list(zip(policy_ids, doc_word_maps, strict=True)), self._now())

            totals = engine.aggregate_word_totals(doc_word_maps)
            top_words = engine.top_words_from_totals(totals, self._top_words_default)
            relations = engine.compute_cooccurrence(doc_words, top_words)
            repository.store_relations(task_id, relations, self._now())

            repository.add_log(
                task_id,
                "info",
                "词频分析完成。",
                {"words": len(totals), "relations": len(relations)},
            )
            return repository.finish(task_id, AnalysisTaskStatus.SUCCEEDED, self._now())
        except AnalysisRepositoryError as error:
            _logger.exception("分析任务 %s 仓储错误: %s", task_id, error.code)
            self._safe_log(repository, task_id, "error", f"分析失败: {error}", {"code": error.code})
            return repository.finish(
                task_id, AnalysisTaskStatus.FAILED, self._now(), error_summary=str(error)
            )
        except Exception as error:  # noqa: BLE001 - runner must never leak
            _logger.exception("分析任务 %s 执行异常", task_id)
            self._safe_log(repository, task_id, "error", f"分析异常: {error}", {})
            return repository.finish(
                task_id, AnalysisTaskStatus.FAILED, self._now(), error_summary="分析执行异常。"
            )

    @staticmethod
    def _safe_log(
        repository: AnalysisRepository, task_id: int, level: str, message: str, context: dict[str, object]
    ) -> None:
        with contextlib.suppress(AnalysisRepositoryError):
            repository.add_log(task_id, level, message, context)


__all__ = ["AnalysisRunner"]
