from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from policy_analysis.analysis.repository import AnalysisRepository
from policy_analysis.analysis.runner import AnalysisRunner
from policy_analysis.auth.models import PagePermission, User
from policy_analysis.policies.models import Policy
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def csrf(client: TestClient) -> dict[str, str]:
    return client.csrf_headers  # type: ignore[attr-defined, no-any-return]


def grant_analysis_page(database_sessions: sessionmaker[Session], username: str = "reader") -> None:
    with database_sessions.begin() as database:
        user = database.scalar(select(User).where(User.username == username))
        assert user is not None
        database.add(PagePermission(user_id=user.id, page_code="analysis"))


class FakeWorker:
    is_started = True
    can_run_tasks = True

    def __init__(self) -> None:
        self.submitted = 0

    def submit_next(self) -> None:
        self.submitted += 1


def _run_task(database_sessions: sessionmaker[Session], task_id: int) -> None:
    repository = AnalysisRepository(database_sessions)
    repository.claim_next(NOW)
    AnalysisRunner(database_sessions, now=lambda: NOW).run_claimed(task_id)


def test_analysis_api_create_status_results_logs(
    admin_client: TestClient,
    auth_app,
    database_sessions: sessionmaker[Session],
    policy_id: int,
) -> None:
    worker = FakeWorker()
    auth_app.state.analysis_worker = worker
    created = admin_client.post(
        "/api/v1/analysis/tasks",
        json={"policy_ids": [policy_id]},
        headers=csrf(admin_client),
    )
    assert created.status_code == 200
    assert worker.submitted == 1
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "pending"

    _run_task(database_sessions, task_id)

    status = admin_client.get(f"/api/v1/analysis/tasks/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "succeeded"

    words = admin_client.get(f"/api/v1/analysis/tasks/{task_id}/words?top=10")
    assert words.status_code == 200
    assert len(words.json()["items"]) > 0

    relations = admin_client.get(f"/api/v1/analysis/tasks/{task_id}/relations?top=10")
    assert relations.status_code == 200

    logs = admin_client.get(f"/api/v1/analysis/tasks/{task_id}/logs")
    assert logs.status_code == 200
    assert logs.json()["total"] >= 1


def test_analysis_api_lists_history(admin_client: TestClient, auth_app, policy_id: int) -> None:
    auth_app.state.analysis_worker = FakeWorker()
    admin_client.post(
        "/api/v1/analysis/tasks",
        json={"policy_ids": [policy_id]},
        headers=csrf(admin_client),
    )
    listed = admin_client.get("/api/v1/analysis/tasks")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_comparison_api_generates_difference_report(
    admin_client: TestClient,
    auth_app,
    database_sessions: sessionmaker[Session],
    policy_id: int,
) -> None:
    with database_sessions.begin() as database:
        original = database.get(Policy, policy_id)
        assert original is not None
        second = Policy(
            source_id=original.source_id,
            category_id=original.category_id,
            title="人工智能安全治理规划",
            canonical_url="https://www.news.cn/ai2.htm",
            publisher="网信办",
            published_at=NOW,
            content_text="推动人工智能产业高质量发展，加强数据安全和算法治理。",
            content_hash="b" * 64,
            webfetch_artifact_id="art-2",
            first_crawled_at=NOW,
            last_crawled_at=NOW,
        )
        database.add(second)
        database.flush()
        second_id = second.id

    auth_app.state.analysis_worker = FakeWorker()
    created = admin_client.post(
        "/api/v1/analysis/comparison-tasks",
        json={"policy_ids": [policy_id, second_id]},
        headers=csrf(admin_client),
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    _run_task(database_sessions, task_id)

    task = admin_client.get(f"/api/v1/analysis/tasks/{task_id}")
    assert task.json()["task_type"] == "policy_comparison"
    report = admin_client.get(f"/api/v1/analysis/tasks/{task_id}/comparison-report")
    assert report.status_code == 200
    assert report.json()["task_id"] == task_id
    assert len(report.json()["policies"]) == 2
    assert len(report.json()["pair_differences"]) == 1


def test_comparison_api_requires_two_distinct_policies(
    admin_client: TestClient, auth_app, policy_id: int
) -> None:
    auth_app.state.analysis_worker = FakeWorker()
    response = admin_client.post(
        "/api/v1/analysis/comparison-tasks",
        json={"policy_ids": [policy_id, policy_id]},
        headers=csrf(admin_client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "COMPARISON_REQUIRES_TWO_POLICIES"


def test_analysis_api_permissions_and_csrf(
    client: TestClient,
    admin_client: TestClient,
    user_client: TestClient,
    database_sessions: sessionmaker[Session],
    policy_id: int,
) -> None:
    assert client.get("/api/v1/analysis/tasks").status_code == 401
    assert user_client.get("/api/v1/analysis/tasks").status_code == 403
    grant_analysis_page(database_sessions)
    assert user_client.get("/api/v1/analysis/tasks").status_code == 200
    assert admin_client.post("/api/v1/analysis/tasks", json={"policy_ids": [policy_id]}).status_code == 403
    assert (
        admin_client.post(
            "/api/v1/analysis/tasks", json={"policy_ids": []}, headers=csrf(admin_client)
        ).status_code
        == 422
    )
    missing = admin_client.post(
        "/api/v1/analysis/tasks",
        json={"policy_ids": [999999]},
        headers=csrf(admin_client),
    )
    assert missing.status_code == 404


def test_analysis_api_rejects_over_schema_limit(admin_client: TestClient, auth_app, policy_id: int) -> None:
    auth_app.state.analysis_worker = FakeWorker()
    response = admin_client.post(
        "/api/v1/analysis/tasks",
        json={"policy_ids": [policy_id] * 101},
        headers=csrf(admin_client),
    )
    assert response.status_code == 422


def test_analysis_api_rejects_over_configured_limit(
    admin_client: TestClient, auth_app, policy_id: int
) -> None:
    auth_app.state.analysis_worker = FakeWorker()
    settings = auth_app.state.settings
    analysis = settings.analysis.model_copy(update={"max_policies_per_task": 1})
    auth_app.state.settings = settings.model_copy(update={"analysis": analysis})
    response = admin_client.post(
        "/api/v1/analysis/tasks",
        json={"policy_ids": [policy_id, policy_id]},
        headers=csrf(admin_client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ANALYSIS_TOO_MANY_POLICIES"
