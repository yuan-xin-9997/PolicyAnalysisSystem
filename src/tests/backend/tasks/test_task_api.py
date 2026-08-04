from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from policy_analysis.auth.models import PagePermission, User
from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source
from policy_analysis.tasks.models import CrawlTaskItem
from policy_analysis.tasks.repository import TaskRepository
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


@pytest.fixture
def rule_id(database_sessions: sessionmaker[Session]) -> int:
    with database_sessions.begin() as database:
        category = PolicyCategory(code="politburo", name="政治局", description=None, is_active=True)
        source = Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        database.add_all([category, source])
        database.flush()
        rule = CollectionRule(
            source_id=source.id,
            category_id=category.id,
            name="中央政治局会议",
            include_keywords_json='["会议"]',
            exclude_keywords_json="[]",
            history_years=5,
            discovery_config_json=json.dumps(
                {"rss_urls": ["https://www.news.cn/rss.xml"], "channel_urls": []}
            ),
            is_active=True,
        )
        database.add(rule)
        database.flush()
        return rule.id


def csrf(client: TestClient) -> dict[str, str]:
    return client.csrf_headers  # type: ignore[attr-defined, no-any-return]


def grant_tasks_page(database_sessions: sessionmaker[Session], username: str = "reader") -> None:
    with database_sessions.begin() as database:
        user = database.scalar(select(User).where(User.username == username))
        assert user is not None
        database.add(PagePermission(user_id=user.id, page_code="tasks"))


def test_admin_creates_lists_reads_cancels_and_pages_task_logs(
    admin_client: TestClient,
    database_sessions: sessionmaker[Session],
    auth_app,
    rule_id: int,
) -> None:
    class FakeWorker:
        is_started = True

        def __init__(self) -> None:
            self.submitted = 0

        def submit_next(self) -> None:
            self.submitted += 1

    worker = FakeWorker()
    auth_app.state.task_worker = worker
    created = admin_client.post(
        "/api/v1/tasks",
        json={"rule_id": rule_id},
        headers=csrf(admin_client),
    )
    assert created.status_code == 201
    assert worker.submitted == 1
    task_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["progress"] == {"processed": 0, "discovered": 0}

    repository = TaskRepository(database_sessions)
    repository.add_log(task_id, "info", "started", {"page": 1})
    repository.add_log(task_id, "warning", "next", {"page": 2})
    with database_sessions.begin() as database:
        database.add(
            CrawlTaskItem(
                task_id=task_id,
                candidate_url="https://www.news.cn/a.html",
                normalized_url="https://www.news.cn/a.html",
                status="filtered",
                reason_code="NO_KEYWORD",
                reason_message="未命中",
            )
        )

    listed = admin_client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [task_id]

    detail = admin_client.get(f"/api/v1/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["counts"] == {
        "success": 0,
        "duplicate": 0,
        "filtered": 0,
        "failed": 0,
        "total_terminal_items": 0,
    }

    logs = admin_client.get(f"/api/v1/tasks/{task_id}/logs?page=1&page_size=1")
    assert logs.status_code == 200
    assert logs.json()["total"] == 2
    assert logs.json()["items"][0]["message"] == "next"

    items = admin_client.get(f"/api/v1/tasks/{task_id}/items")
    assert items.status_code == 200
    assert items.json()["items"][0]["reason_code"] == "NO_KEYWORD"

    cancelled = admin_client.post(f"/api/v1/tasks/{task_id}/cancel", headers=csrf(admin_client))
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested_at"] is not None


def test_task_api_permissions_and_csrf(
    client: TestClient,
    admin_client: TestClient,
    user_client: TestClient,
    database_sessions: sessionmaker[Session],
    rule_id: int,
) -> None:
    task = TaskRepository(database_sessions).create_task(rule_id, "manual", {"kind": "manual"}, NOW)
    assert client.get("/api/v1/tasks").status_code == 401
    assert user_client.get("/api/v1/tasks").status_code == 403

    grant_tasks_page(database_sessions)
    assert user_client.get("/api/v1/tasks").status_code == 200
    denied_create = user_client.post("/api/v1/tasks", json={"rule_id": rule_id}, headers=csrf(user_client))
    assert denied_create.status_code == 403
    assert admin_client.post("/api/v1/tasks", json={"rule_id": rule_id}).status_code == 403
    assert user_client.post(f"/api/v1/tasks/{task.id}/cancel", headers=csrf(user_client)).status_code == 403
