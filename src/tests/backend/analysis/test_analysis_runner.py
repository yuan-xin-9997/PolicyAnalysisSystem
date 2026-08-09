from __future__ import annotations

from datetime import UTC, datetime

from policy_analysis.analysis.models import AnalysisTask, AnalysisTaskStatus
from policy_analysis.analysis.repository import AnalysisRepository
from policy_analysis.analysis.runner import AnalysisRunner
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def test_runner_succeeds_and_stores_results(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.claim_next(NOW)
    runner = AnalysisRunner(database_sessions, min_word_length=2, top_words_default=10, now=lambda: NOW)
    status = runner.run_claimed(task.id)
    assert status == AnalysisTaskStatus.SUCCEEDED
    assert repository.get(task.id).status == "succeeded"
    words = repository.list_words(task.id, top=10)
    assert any(word["word"] == "人工智能" for word in words)
    _relations, nodes = repository.list_relations(task.id, top=10)
    assert len(nodes) > 0
    logs, _ = repository.list_logs(task.id)
    assert any("完成" in log.message for log in logs)


def test_runner_fails_when_no_policies(database_sessions: sessionmaker[Session]) -> None:
    with database_sessions.begin() as database:
        task = AnalysisTask(
            task_type="word_frequency",
            status="pending",
            policy_count=0,
            request_snapshot_json='{"policy_ids":[]}',
        )
        database.add(task)
        database.flush()
        task_id = task.id
    repository = AnalysisRepository(database_sessions)
    repository.claim_next(NOW)
    runner = AnalysisRunner(database_sessions, now=lambda: NOW)
    status = runner.run_claimed(task_id)
    assert status == AnalysisTaskStatus.FAILED
    assert repository.get(task_id).status == "failed"
