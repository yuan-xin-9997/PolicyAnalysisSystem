from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_analysis.auth.models import PagePermission, User
from policy_analysis.auth.service import AuthService, UserSyncService
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.main import create_app
from policy_analysis.policies.models import Policy
from policy_analysis.policies.schemas import PolicyWrite
from policy_analysis.policies.service import PolicyService
from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def policy_api_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    password_hasher: PasswordHasher,
    mutable_clock,
) -> Iterator[tuple[FastAPI, sessionmaker[Session], Path, Engine]]:
    database_path = tmp_path / "policy-api.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(database_path))
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    engine = build_engine(database_path)
    sessions = session_factory(engine)
    password_file = tmp_path / "password.txt"
    password_file.write_text(
        "admin:admin123:admin\nreader:reader123:user\n",
        encoding="utf-8",
    )
    os.chmod(password_file, 0o600)
    auth_service = AuthService(
        sessions=sessions,
        user_sync=UserSyncService(password_file, sessions, password_hasher),
        password_hasher=password_hasher,
        session_hours=12,
        secure_cookie=False,
        login_attempts=5,
        login_window_seconds=60,
        login_max_active_keys=128,
        now=mutable_clock.now,
        monotonic=mutable_clock.monotonic,
    )
    app = create_app(auth_service=auth_service, project_root=tmp_path)
    try:
        yield app, sessions, password_file, engine
    finally:
        engine.dispose()


@pytest.fixture
def policy_records(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
) -> dict[str, object]:
    _app, sessions, _password_file, _engine = policy_api_runtime
    with sessions.begin() as database:
        xinhua = Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        government = Source(
            code="government",
            name="政府网",
            organization="中国政府网",
            base_url="https://www.gov.cn/",
            adapter_type="government",
            allowed_domains_json='["gov.cn"]',
            is_active=True,
        )
        meeting = PolicyCategory(code="meeting", name="重要会议", is_active=True)
        economy = PolicyCategory(code="economy", name="经济工作", is_active=True)
        database.add_all([xinhua, government, meeting, economy])
        database.flush()
        rule = CollectionRule(
            source_id=xinhua.id,
            category_id=meeting.id,
            name="API 测试规则",
            include_keywords_json='["中共中央政治局召开会议"]',
            exclude_keywords_json="[]",
            history_years=5,
            discovery_config_json='{"rss_urls":["https://www.news.cn/rss.xml"]}',
            is_active=True,
        )
        database.add(rule)
        database.flush()
        task = CrawlTask(
            rule_id=rule.id,
            trigger_type="manual",
            status="running",
            request_snapshot_json="{}",
        )
        database.add(task)
        database.flush()
        items = [
            CrawlTaskItem(
                task_id=task.id,
                candidate_url=f"https://www.news.cn/politics/api-{index}.html",
                status="stored",
            )
            for index in range(3)
        ]
        database.add_all(items)
        database.flush()
        ids = {
            "xinhua": xinhua.id,
            "government": government.id,
            "meeting": meeting.id,
            "economy": economy.id,
            "task_id": task.id,
            "item_ids": [item.id for item in items],
        }

    service = PolicyService(sessions)
    values = [
        _write(
            int(ids["xinhua"]),
            int(ids["meeting"]),
            title="中共中央政治局召开会议 研究经济工作",
            url="https://www.news.cn/politics/20260730/economy.html",
            publisher="新华社",
            published_at=datetime(2026, 7, 30, 14, tzinfo=SHANGHAI),
            content="会议分析当前经济形势，强调推动高质量发展。",
            crawled_at=datetime(2026, 7, 31, 12, tzinfo=SHANGHAI),
            artifact="artifact-api-1",
        ),
        _write(
            int(ids["xinhua"]),
            int(ids["economy"]),
            title="中共中央政治局召开会议 审议重要文件",
            url="https://www.news.cn/politics/20260729/innovation.html",
            publisher="新华网",
            published_at=datetime(2026, 7, 29, 10, tzinfo=SHANGHAI),
            content="<script>window.hacked=true</script>会议强调推动科技创新。",
            crawled_at=datetime(2026, 7, 31, 13, tzinfo=SHANGHAI),
            artifact="artifact-api-2",
        ),
        _write(
            int(ids["government"]),
            int(ids["economy"]),
            title="国务院召开经济形势座谈会",
            url="https://www.gov.cn/zhengce/20260728/example.html",
            publisher="中国政府网",
            published_at=datetime(2026, 7, 28, 9, tzinfo=SHANGHAI),
            content="部署民生保障工作和区域协调发展。",
            crawled_at=datetime(2026, 8, 1, 9, tzinfo=SHANGHAI),
            artifact="artifact-api-3",
        ),
    ]
    policy_ids: list[int] = []
    for record, item_id in zip(values, ids["item_ids"], strict=True):
        outcome = service.upsert(record, task_item_id=int(item_id))
        policy_ids.append(outcome.policy_id)
    with sessions.begin() as database:
        for item_id, policy_id in zip(ids["item_ids"], policy_ids, strict=True):
            item = database.get(CrawlTaskItem, item_id)
            assert item is not None
            item.policy_id = policy_id
    return {**ids, "policy_ids": policy_ids}


def _write(
    source_id: int,
    category_id: int,
    *,
    title: str,
    url: str,
    publisher: str,
    published_at: datetime,
    content: str,
    crawled_at: datetime,
    artifact: str,
) -> PolicyWrite:
    return PolicyWrite(
        source_id=source_id,
        category_id=category_id,
        title=title,
        canonical_url=url,
        publisher=publisher,
        published_at=published_at,
        content_text=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        webfetch_artifact_id=artifact,
        crawled_at=crawled_at,
    )


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def test_policy_reads_require_session_and_policies_page_permission(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, sessions, _password_file, _engine = policy_api_runtime
    policy_id = policy_records["policy_ids"][0]  # type: ignore[index]
    with client_context(app) as anonymous:
        assert anonymous.get("/api/v1/policies").status_code == 401
        assert anonymous.get(f"/api/v1/policies/{policy_id}").status_code == 401
    with client_context(app) as reader:
        _login(reader, "reader", "reader123")
        assert reader.get("/api/v1/policies").status_code == 403
        with sessions.begin() as database:
            user = database.scalar(select(User).where(User.username == "reader"))
            assert user is not None
            database.add(PagePermission(user_id=user.id, page_code="policies"))
        assert reader.get("/api/v1/policies").status_code == 200
        assert reader.get(f"/api/v1/policies/{policy_id}").status_code == 200
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        assert admin.get("/api/v1/policies").status_code == 200


@pytest.mark.parametrize(
    ("keyword", "expected_titles"),
    [
        ("经济", ["中共中央政治局召开会议 研究经济工作", "国务院召开经济形势座谈会"]),
        ("经济工作", ["中共中央政治局召开会议 研究经济工作"]),
        ("科技创新", ["中共中央政治局召开会议 审议重要文件"]),
    ],
)
def test_policy_keyword_search_supports_short_and_long_chinese_terms(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
    keyword: str,
    expected_titles: list[str],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get("/api/v1/policies", params={"keyword": keyword})
    assert response.status_code == 200
    assert response.json()["total"] == len(expected_titles)
    assert [item["title"] for item in response.json()["items"]] == expected_titles


@pytest.mark.parametrize(
    ("parameters", "expected_title"),
    [
        ({"publisher": "新华网"}, "中共中央政治局召开会议 审议重要文件"),
        ({"category_id": "1"}, "中共中央政治局召开会议 研究经济工作"),
        ({"source_id": "2"}, "国务院召开经济形势座谈会"),
        (
            {
                "published_from": "2026-07-29T00:00:00+08:00",
                "published_to": "2026-07-29T23:59:59+08:00",
            },
            "中共中央政治局召开会议 审议重要文件",
        ),
        (
            {
                "crawled_from": "2026-08-01T00:00:00+08:00",
                "crawled_to": "2026-08-01T23:59:59+08:00",
            },
            "国务院召开经济形势座谈会",
        ),
    ],
)
def test_policy_list_filters_each_supported_dimension(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
    parameters: dict[str, str],
    expected_title: str,
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get("/api/v1/policies", params=parameters)
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == [expected_title]


def test_policy_list_combines_filters_and_keeps_count_pagination_consistent(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    params = {
        "keyword": "中共中央政治局",
        "publisher": "新华社",
        "category_id": policy_records["meeting"],
        "source_id": policy_records["xinhua"],
        "published_from": "2026-07-30T00:00:00+08:00",
        "published_to": "2026-07-30T23:59:59+08:00",
        "crawled_from": "2026-07-31T00:00:00+08:00",
        "crawled_to": "2026-07-31T23:59:59+08:00",
        "page": 1,
        "page_size": 1,
    }
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get("/api/v1/policies", params=params)
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["title"].endswith("研究经济工作")


def test_policy_list_has_configured_limit_stable_sort_and_tie_breaker(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, sessions, _password_file, _engine = policy_api_runtime
    with sessions.begin() as database:
        policies = list(database.scalars(select(Policy).order_by(Policy.id)))
        policies[1].published_at = policies[0].published_at
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        app.state.settings = type(
            "SettingsWithPagination",
            (),
            {"pagination": type("Pagination", (), {"default_page_size": 2, "max_page_size": 2})()},
        )()
        first = admin.get(
            "/api/v1/policies",
            params={"page": 1, "page_size": 2, "sort_by": "published_at", "sort_order": "desc"},
        )
        second = admin.get(
            "/api/v1/policies",
            params={"page": 2, "page_size": 2, "sort_by": "published_at", "sort_order": "desc"},
        )
        oversized = admin.get("/api/v1/policies", params={"page_size": 3})
    assert first.status_code == second.status_code == 200
    ids = [item["id"] for item in first.json()["items"] + second.json()["items"]]
    policy_ids = policy_records["policy_ids"]
    assert ids == [policy_ids[1], policy_ids[0], policy_ids[2]]  # type: ignore[index]
    assert first.json()["total"] == second.json()["total"] == 3
    assert oversized.status_code == 422


def test_policy_list_sorts_by_last_crawled_at_with_stable_tie_breaker(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get(
            "/api/v1/policies",
            params={"sort_by": "last_crawled_at", "sort_order": "asc"},
        )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == policy_records["policy_ids"]


def test_policy_detail_is_plain_json_text_and_hides_internal_fields(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    policy_id = policy_records["policy_ids"][1]  # type: ignore[index]
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get(f"/api/v1/policies/{policy_id}")
    body = response.json()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert body["content_text"].startswith("<script>window.hacked=true</script>")
    assert body["category"] == {"id": policy_records["economy"], "code": "economy", "name": "经济工作"}
    assert body["source"] == {"id": policy_records["xinhua"], "code": "xinhua", "name": "新华网"}
    assert body["latest_task_id"] == policy_records["task_id"]
    assert body["published_at"].endswith(("Z", "+00:00"))
    serialized = json.dumps(body, ensure_ascii=False)
    for hidden in ("webfetch_artifact_id", "artifact-api-2", "allowed_domains_json", "_sa_instance_state"):
        assert hidden not in serialized


def test_missing_policy_uses_safe_404_envelope(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get("/api/v1/policies/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POLICY_NOT_FOUND"
    assert "sql" not in response.text.lower()
    assert str(PROJECT_ROOT) not in response.text


@pytest.mark.parametrize("keyword", ['"', "OR", "NEAR(", "*", "%", "_", "\\"])
def test_policy_api_never_exposes_fts_syntax_errors(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
    keyword: str,
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app, raise_server_exceptions=False) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get("/api/v1/policies", params={"keyword": keyword})
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    "query",
    [
        "unknown=value",
        "offset=0",
        "page=0",
        "page_size=0",
        "sort_by=content_hash",
        "sort_order=random",
        "keyword=%20%20%20",
        "publisher=%20新华社",
        "published_from=2026-07-30T12:00:00",
        "published_from=2026-08-01T00:00:00%2B08:00&published_to=2026-07-01T00:00:00%2B08:00",
        "crawled_from=2026-08-01T00:00:00%2B08:00&crawled_to=2026-07-01T00:00:00%2B08:00",
    ],
)
def test_policy_list_rejects_unknown_or_ambiguous_query_values(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
    query: str,
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        response = admin.get(f"/api/v1/policies?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_read_only_policy_routes_do_not_require_csrf(
    policy_api_runtime: tuple[FastAPI, sessionmaker[Session], Path, Engine],
    policy_records: dict[str, object],
    client_context: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    app, _sessions, _password_file, _engine = policy_api_runtime
    policy_id = policy_records["policy_ids"][0]  # type: ignore[index]
    with client_context(app) as admin:
        _login(admin, "admin", "admin123")
        assert admin.get("/api/v1/policies", headers={"X-CSRF-Token": "wrong"}).status_code == 200
        assert admin.get(f"/api/v1/policies/{policy_id}").status_code == 200
