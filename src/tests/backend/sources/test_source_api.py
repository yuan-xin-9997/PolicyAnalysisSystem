from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from policy_analysis.auth.models import PagePermission, User
from policy_analysis.sources.models import PolicyCategory, Source
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def api_catalog(database_sessions: sessionmaker[Session]) -> None:
    with database_sessions.begin() as database:
        database.add_all(
            [
                PolicyCategory(
                    code="politburo_meeting",
                    name="中央政治局会议",
                    description="新华社中央政治局会议通报",
                    is_active=True,
                ),
                Source(
                    code="xinhua",
                    name="新华网",
                    organization="新华社",
                    base_url="https://www.news.cn/",
                    adapter_type="xinhua",
                    allowed_domains_json='["news.cn", "xinhuanet.com"]',
                    is_active=True,
                ),
            ]
        )


def csrf(client: TestClient) -> dict[str, str]:
    return client.csrf_headers  # type: ignore[attr-defined, no-any-return]


def rule_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "中央政治局会议",
        "source_code": "xinhua",
        "category_code": "politburo_meeting",
        "include_keywords": ["中共中央政治局召开会议"],
        "exclude_keywords": ["视频"],
        "history_years": 5,
        "discovery": {"rss_urls": ["https://www.news.cn/politics/rss.xml"]},
    }
    payload.update(overrides)
    return payload


def test_admin_can_read_and_manage_rules_and_schedules(admin_client: TestClient) -> None:
    categories = admin_client.get("/api/v1/policy-categories")
    sources = admin_client.get("/api/v1/sources")
    assert categories.status_code == sources.status_code == 200
    assert categories.json()[0]["code"] == "politburo_meeting"
    assert sources.json()[0]["allowed_domains"] == ["news.cn", "xinhuanet.com"]
    assert "allowed_domains_json" not in sources.text

    created = admin_client.post("/api/v1/collection-rules", json=rule_payload(), headers=csrf(admin_client))
    assert created.status_code == 201
    assert created.json()["history_years"] == 5
    assert created.json()["source"]["code"] == "xinhua"
    rule_id = created.json()["id"]

    listed = admin_client.get("/api/v1/collection-rules")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [rule_id]
    assert "_json" not in listed.text

    patched = admin_client.patch(
        f"/api/v1/collection-rules/{rule_id}",
        json={"name": "更新会议规则"},
        headers=csrf(admin_client),
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "更新会议规则"

    scheduled = admin_client.post(
        "/api/v1/schedules",
        json={"rule_id": rule_id, "cron_expression": "0 9 * * *"},
        headers=csrf(admin_client),
    )
    assert scheduled.status_code == 201
    assert scheduled.json()["is_active"] is False
    assert scheduled.json()["next_run_at"] is None
    schedule_id = scheduled.json()["id"]

    enabled = admin_client.patch(
        f"/api/v1/schedules/{schedule_id}",
        json={"is_active": True},
        headers=csrf(admin_client),
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True
    assert enabled.json()["timezone"] == "Asia/Shanghai"
    assert enabled.json()["next_run_at"].endswith(("Z", "+00:00"))
    listed_schedules = admin_client.get("/api/v1/schedules")
    assert [item["id"] for item in listed_schedules.json()] == [schedule_id]


def test_read_endpoints_require_authentication_and_tasks_permission(
    client: TestClient,
    user_client: TestClient,
    database_sessions: sessionmaker[Session],
) -> None:
    paths = [
        "/api/v1/policy-categories",
        "/api/v1/sources",
        "/api/v1/collection-rules",
        "/api/v1/schedules",
    ]
    for path in paths:
        assert client.get(path).status_code == 401
        assert user_client.get(path).status_code == 403

    with database_sessions.begin() as database:
        user = database.scalar(select(User).where(User.username == "reader"))
        assert user is not None
        database.add(PagePermission(user_id=user.id, page_code="tasks"))

    for path in paths:
        assert user_client.get(path).status_code == 200


def test_writes_require_admin_and_csrf(
    admin_client: TestClient,
    user_client: TestClient,
    database_sessions: sessionmaker[Session],
) -> None:
    without_csrf = admin_client.post("/api/v1/collection-rules", json=rule_payload())
    assert without_csrf.status_code == 403

    with database_sessions.begin() as database:
        user = database.scalar(select(User).where(User.username == "reader"))
        assert user is not None
        database.add(PagePermission(user_id=user.id, page_code="tasks"))
    denied = user_client.post("/api/v1/collection-rules", json=rule_payload(), headers=csrf(user_client))
    assert denied.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload", "expected_status"),
    [
        ("post", "/api/v1/schedules", {"rule_id": 999, "cron_expression": "0 9 * *"}, 422),
        (
            "post",
            "/api/v1/collection-rules",
            rule_payload(discovery={"rss_urls": ["https://news.cn.evil.example/rss"]}),
            422,
        ),
        ("patch", "/api/v1/collection-rules/999", {}, 422),
        ("patch", "/api/v1/collection-rules/999", {"name": "有效名称"}, 404),
        ("patch", "/api/v1/schedules/999", {"is_active": True}, 404),
    ],
)
def test_api_maps_invalid_and_missing_resources_to_safe_error_envelopes(
    admin_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, object],
    expected_status: int,
) -> None:
    response = getattr(admin_client, method)(path, json=payload, headers=csrf(admin_client))
    assert response.status_code == expected_status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id", "details"}
    serialized = json.dumps(body, ensure_ascii=False)
    assert "sqlite" not in serialized.lower()
    assert "/Users/" not in serialized
    assert "csrf" not in serialized.lower()
    assert "session" not in serialized.lower()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/policy-categories?unexpected=1",
        "/api/v1/sources?unexpected=1",
        "/api/v1/collection-rules?unexpected=1",
        "/api/v1/schedules?unexpected=1",
    ],
)
def test_list_endpoints_reject_undeclared_query_parameters(admin_client: TestClient, path: str) -> None:
    response = admin_client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_schema_rejects_client_controlled_schedule_fields_and_extra_rule_fields(
    admin_client: TestClient,
) -> None:
    rule = admin_client.post(
        "/api/v1/collection-rules", json=rule_payload(), headers=csrf(admin_client)
    ).json()
    for extra in [
        {"timezone": "UTC"},
        {"is_active": True},
        {"next_run_at": "2026-08-01T00:00:00Z"},
        {"last_run_at": "2026-08-01T00:00:00Z"},
    ]:
        response = admin_client.post(
            "/api/v1/schedules",
            json={"rule_id": rule["id"], "cron_expression": "0 9 * * *", **extra},
            headers=csrf(admin_client),
        )
        assert response.status_code == 422

    extra_rule = admin_client.post(
        "/api/v1/collection-rules",
        json={**rule_payload(), "unknown": "secret"},
        headers=csrf(admin_client),
    )
    assert extra_rule.status_code == 422


def test_undefined_methods_and_paths_keep_safe_handlers(admin_client: TestClient) -> None:
    assert admin_client.put("/api/v1/collection-rules/1", json={}).status_code == 405
    assert admin_client.get("/api/v1/not-a-source-route").status_code == 404
