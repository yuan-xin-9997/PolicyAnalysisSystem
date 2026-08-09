from __future__ import annotations

from datetime import UTC, datetime

import pytest
from policy_analysis.analysis.models import AnalysisTaskStatus
from policy_analysis.analysis.repository import AnalysisRepository, AnalysisRepositoryError
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def test_create_task_persists_and_claims(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW, requested_by=None)
    assert task.status == "pending"
    assert task.policy_count == 1
    assert repository.claim_next(NOW) == task.id
    assert repository.get(task.id).status == "running"
    assert repository.claim_next(NOW) is None


def test_create_task_deduplicates_policy_ids(
    database_sessions: sessionmaker[Session], policy_id: int
) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id, policy_id], NOW)
    assert task.policy_count == 1


def test_create_task_rejects_missing_policy(database_sessions: sessionmaker[Session]) -> None:
    repository = AnalysisRepository(database_sessions)
    with pytest.raises(AnalysisRepositoryError, match="部分政策不存在") as raised:
        repository.create_task([999999], NOW)
    assert raised.value.code == "POLICY_NOT_FOUND"


def test_store_results_and_list_words(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.claim_next(NOW)
    repository.store_results(task.id, [(policy_id, {"人工智能": (5, 0.9), "产业": (3, 0.5)})], NOW)
    by_freq = repository.list_words(task.id, top=10, sort_by="frequency")
    assert by_freq[0]["word"] == "人工智能"
    assert by_freq[0]["frequency"] == 5
    assert by_freq[0]["doc_count"] == 1
    by_tfidf = repository.list_words(task.id, top=10, sort_by="tfidf")
    assert by_tfidf[0]["word"] == "人工智能"


def test_list_words_filters_by_policy_id(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.store_results(task.id, [(policy_id, {"人工智能": (5, 0.9)})], NOW)
    assert len(repository.list_words(task.id, top=10, policy_id=policy_id)) == 1
    assert repository.list_words(task.id, top=10, policy_id=999999) == []


def test_store_relations_and_list(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.store_results(
        task.id,
        [(policy_id, {"人工智能": (5, 0.9), "产业": (3, 0.5), "数字经济": (2, 0.4)})],
        NOW,
    )
    repository.store_relations(task.id, [("产业", "人工智能", 3), ("产业", "数字经济", 1)], NOW)
    relations, nodes = repository.list_relations(task.id, top=10)
    assert "人工智能" in nodes
    pairs = {(relation.word1, relation.word2): relation.co_count for relation in relations}
    assert pairs.get(("产业", "人工智能")) == 3


def test_finish_transitions_and_is_idempotent_for_terminal(
    database_sessions: sessionmaker[Session], policy_id: int
) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.claim_next(NOW)
    assert repository.finish(task.id, AnalysisTaskStatus.SUCCEEDED, NOW) == AnalysisTaskStatus.SUCCEEDED
    assert repository.get(task.id).status == "succeeded"
    assert repository.get(task.id).finished_at is not None
    assert repository.finish(task.id, AnalysisTaskStatus.FAILED, NOW) == AnalysisTaskStatus.SUCCEEDED


def test_recover_interrupted_marks_running_failed(
    database_sessions: sessionmaker[Session], policy_id: int
) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.claim_next(NOW)
    ids = repository.recover_interrupted(NOW)
    assert task.id in ids
    assert repository.get(task.id).status == "failed"


def test_add_log_and_list(database_sessions: sessionmaker[Session], policy_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    task = repository.create_task([policy_id], NOW)
    repository.add_log(task.id, "info", "started", {"k": "v"})
    logs, total = repository.list_logs(task.id)
    assert total == 1
    assert logs[0].message == "started"
