from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import httpx
import policy_analysis.main as main_module
from fastapi.testclient import TestClient
from policy_analysis.collectors.webfetch import WebFetchClient
from policy_analysis.main import create_app
from policy_analysis.sources.models import PolicyCategory, Source

API_KEY = "integration-webfetch-key"
ARTICLE_URL = "https://news.cn/politics/20260730/c.html"
RSS_URL = "https://news.cn/politics/rss.xml"


def test_manual_collection_flow_stores_policy_and_second_run_deduplicates(
    client_context: Callable[..., AbstractContextManager[TestClient]],
    project_root: Path,
    monkeypatch,
) -> None:
    runtime_directory = project_root / "runtime"
    runtime_directory.mkdir()
    password_file = runtime_directory / "password.txt"
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    os.chmod(password_file, 0o600)

    config_path = project_root / "config" / "app.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "database": {"path": "runtime/app.sqlite3"},
                "auth": {"password_file": "runtime/password.txt"},
                "webfetch": {"base_url": "http://webfetch.integration"},
                "tasks": {"max_workers": 1, "retry_attempts": 1},
            }
        ),
        encoding="utf-8",
    )
    transport = httpx.MockTransport(_webfetch_handler)

    def webfetch_factory(base_url: str, api_key: str, **kwargs: object) -> WebFetchClient:
        return WebFetchClient(
            base_url,
            api_key,
            transport=transport,
            sleep=lambda _seconds: None,
            **kwargs,
        )

    monkeypatch.setattr(main_module, "WebFetchClient", webfetch_factory)
    app = create_app(
        project_root=project_root,
        config_path=Path("config/app.json"),
        environment={
            "POLICY_ANALYSIS_WEBFETCH__API_KEY": API_KEY,
            "POLICY_ANALYSIS_AUTH__SESSION_SECRET": "integration-session-secret",
        },
    )

    with client_context(app) as client:
        _seed_catalog(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}

        rule_id = _create_rule(client, csrf)
        first_task_id = _trigger_task(client, csrf, rule_id)
        first_task = _wait_for_terminal_task(client, first_task_id)
        assert first_task["status"] == "succeeded"
        assert first_task["counts"]["success"] == 1

        policies = client.get("/api/v1/policies", params={"keyword": "中共中央政治局"})
        assert policies.status_code == 200
        policy_payload = policies.json()
        assert policy_payload["total"] == 1
        assert policy_payload["items"][0]["title"] == "中共中央政治局召开会议"

        second_task_id = _trigger_task(client, csrf, rule_id)
        second_task = _wait_for_terminal_task(client, second_task_id)
        assert second_task["status"] == "succeeded"
        assert second_task["counts"]["duplicate"] == 1

        repeated = client.get("/api/v1/policies", params={"keyword": "中共中央政治局"})
        assert repeated.status_code == 200
        assert repeated.json()["total"] == 1

        items = client.get(f"/api/v1/tasks/{second_task_id}/items")
        logs = client.get(f"/api/v1/tasks/{second_task_id}/logs")
        assert items.status_code == logs.status_code == 200
        assert items.json()["items"][0]["status"] == "duplicate"
        assert any(log["message"] for log in logs.json()["items"])


def _create_rule(client: TestClient, csrf: dict[str, str]) -> int:
    response = client.post(
        "/api/v1/collection-rules",
        headers=csrf,
        json={
            "name": "中央政治局会议集成测试",
            "source_code": "xinhua",
            "category_code": "politburo_meeting",
            "include_keywords": ["中共中央政治局召开会议"],
            "exclude_keywords": ["视频"],
            "history_years": 5,
            "discovery": {"rss_urls": [RSS_URL], "channel_urls": []},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _seed_catalog(app) -> None:
    with app.state.database_sessions.begin() as database:
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
                    base_url="https://news.cn/",
                    adapter_type="xinhua",
                    allowed_domains_json='["news.cn", "xinhuanet.com"]',
                    is_active=True,
                ),
            ]
        )


def _trigger_task(client: TestClient, csrf: dict[str, str], rule_id: int) -> int:
    response = client.post("/api/v1/tasks", headers=csrf, json={"rule_id": rule_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _wait_for_terminal_task(client: TestClient, task_id: int) -> dict[str, object]:
    deadline = time.monotonic() + 5
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] in {"succeeded", "partially_succeeded", "failed", "cancelled"}:
            return last_payload
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach terminal status: {last_payload}")


def _webfetch_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    if request.url.path == "/v1/fetch":
        return httpx.Response(200, json=_fetch_payload())
    if request.url.path == "/v1/extract":
        return httpx.Response(200, json=_article_payload())
    if request.url.path == "/health/ready":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404, json={"success": False})


def _fetch_payload() -> dict[str, object]:
    return {
        "request_id": "fetch-1",
        "success": True,
        "requested_url": RSS_URL,
        "final_url": RSS_URL,
        "status_code": 200,
        "strategy": "httpx",
        "from_cache": False,
        "stale": False,
        "elapsed_ms": 1,
        "content_type": "application/rss+xml",
        "body": (
            "<rss><channel><item><title>中共中央政治局召开会议</title>"
            f"<link>{ARTICLE_URL}</link></item></channel></rss>"
        ),
        "artifact_id": None,
        "fetched_at": "2026-08-01T00:00:00+00:00",
        "attempts": [
            {
                "sequence": 1,
                "strategy": "httpx",
                "status_code": 200,
                "error_code": None,
                "elapsed_ms": 1,
                "upgrade_reason": None,
            }
        ],
    }


def _article_payload() -> dict[str, object]:
    content = (
        "新华社北京7月30日电 中共中央政治局7月30日召开会议，分析研究当前经济形势，"
        "部署下半年经济工作。会议强调，要坚持稳中求进工作总基调，完整、准确、全面贯彻"
        "新发展理念，加大宏观政策调控力度，扎实推动高质量发展，切实保障和改善民生。"
    )
    return {
        "request_id": "article-1",
        "adapter": "generic.article",
        "adapter_version": "1",
        "artifact_id": "artifact-policy-1",
        "data": {
            "title": "中共中央政治局召开会议",
            "content": content,
            "author": "新华社",
            "date": "2026-07-30T14:00:00+08:00",
        },
    }
