from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.collectors.base import ExtractedArticle, WebFetchClientError
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.policies.models import Policy, PolicyRevision
from policy_analysis.policies.service import PolicyService
from policy_analysis.sources.models import CollectionRule, PolicyCategory, SeedUrl, Source
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem, CrawlTaskLog, TaskStatus
from policy_analysis.tasks.repository import TaskRepository, TaskRepositoryError
from policy_analysis.tasks.runner import TaskRunner
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[4]
SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 1, 20, 30, tzinfo=SHANGHAI)


class FakeWebFetch:
    def __init__(self, sessions: sessionmaker[Session], articles: dict[str, ExtractedArticle | Exception]):
        self.sessions = sessions
        self.articles = articles
        self.calls: list[str] = []
        self.active_transactions: list[bool] = []
        self.after_extract = None

    def fetch_text(self, url: str) -> str:
        self._record(url)
        return "<rss><channel></channel></rss>"

    def extract_article(self, url: str) -> ExtractedArticle:
        self._record(url)
        result = self.articles[url]
        if self.after_extract is not None:
            self.after_extract(url)
        if isinstance(result, Exception):
            raise result
        return result

    def _record(self, url: str) -> None:
        self.calls.append(url)
        with self.sessions() as session:
            self.active_transactions.append(session.in_transaction())
            session.execute(text("BEGIN IMMEDIATE"))
            session.rollback()


def article(
    url_date: str, *, content: str | None = None, title: str = "中共中央政治局召开会议"
) -> ExtractedArticle:
    body = content or ("新华社北京8月1日电 中共中央政治局召开会议。" + "重要部署。" * 30)
    return ExtractedArticle("req", "artifact", title, body, "新华社", f"{url_date}T10:00:00+08:00")


@pytest.fixture
def task_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "tasks.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(path))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = build_engine(path)
    sessions = session_factory(engine)
    try:
        yield sessions
    finally:
        engine.dispose()


def catalog(sessions: sessionmaker[Session], *, seed_urls: list[tuple[str, bool, str, date]]):
    with sessions.begin() as db:
        source = Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn","www.news.cn"]',
            is_active=True,
        )
        category = PolicyCategory(code="politburo", name="政治局会议", is_active=True)
        db.add_all([source, category])
        db.flush()
        rule = CollectionRule(
            source_id=source.id,
            category_id=category.id,
            name="会议",
            include_keywords_json='["中共中央政治局召开会议"]',
            exclude_keywords_json='["视频"]',
            history_years=5,
            discovery_config_json='{"rss_urls":[],"channel_urls":["https://www.news.cn/channel"]}',
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        db.add(rule)
        db.flush()
        for url, verified, expected_title, expected_date in seed_urls:
            db.add(
                SeedUrl(
                    rule_id=rule.id,
                    url=url,
                    is_verified=verified,
                    expected_title=expected_title,
                    expected_published_date=expected_date,
                )
            )
        task = CrawlTask(rule_id=rule.id, trigger_type="manual", status="pending", request_snapshot_json="{}")
        db.add(task)
        db.flush()
        return task.id, rule.id


def runner(sessions, client):
    return TaskRunner(
        sessions,
        client,
        PolicyService(sessions, now=lambda: NOW.astimezone(UTC)),
        now=lambda: NOW,
        minimum_content_chars=20,
    )


def test_repository_creates_and_reads_pending_task_with_snapshot(task_db) -> None:
    _task_id, rule_id = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db)

    created = repository.create_task(rule_id, "manual", {"history_years": 5}, NOW)

    stored = repository.get(created.id)
    assert stored is not None
    assert stored.status == "pending"
    snapshot = json.loads(stored.request_snapshot_json)
    assert snapshot["version"] == 1
    assert snapshot["request"] == {"history_years": 5}
    assert snapshot["rule"]["source"]["adapter_type"] == "xinhua"
    assert snapshot["rule"]["history_years"] == 5


def test_runner_uses_frozen_snapshot_after_live_configuration_changes(task_db) -> None:
    seed = "https://www.news.cn/20260801/frozen.html"
    _task_id, rule_id = catalog(
        task_db,
        seed_urls=[(seed, True, "中共中央政治局召开会议", date(2026, 8, 1))],
    )
    repository = TaskRepository(task_db)
    frozen = repository.create_task(rule_id, "manual", {}, NOW)
    with task_db.begin() as db:
        rule = db.get(CollectionRule, rule_id)
        rule.is_active = False
        rule.include_keywords_json = '["被篡改"]'
        rule.source.adapter_type = "unsupported"
        db.query(SeedUrl).filter(SeedUrl.rule_id == rule_id).delete()

    result = runner(task_db, FakeWebFetch(task_db, {seed: article("2026-08-01")})).run(frozen.id)

    assert result.status is TaskStatus.SUCCEEDED


def test_runner_stores_verified_seed_with_real_item_and_short_transactions(task_db) -> None:
    url = "https://www.news.cn/20260801/seed.html"
    task_id, _ = catalog(task_db, seed_urls=[(url, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    client = FakeWebFetch(task_db, {url: article("2026-08-01")})

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.SUCCEEDED
    assert client.calls == ["https://www.news.cn/channel", url, url]
    assert client.active_transactions == [False, False, False]
    with task_db() as db:
        task = db.get(CrawlTask, task_id)
        item = db.scalar(select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id))
        assert (task.discovered_count, task.success_count, task.failed_count) == (1, 1, 0)
        assert (item.status, item.attempt_count, item.policy_id) == ("stored", 1, 1)
        assert db.scalar(select(func.count()).select_from(Policy)) == 1


def test_runner_persists_cleaned_content_and_hash_from_noisy_article(task_db) -> None:
    url = "https://www.news.cn/20260801/seed.html"
    task_id, _ = catalog(task_db, seed_urls=[(url, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    noisy_body = (
        "2026年8月1日 10:00:00 来源：新华社 "
        "新华社北京8月1日电 中共中央政治局召开会议。" + "重要部署。" * 30 + "\n阅读下一篇： 37 其他推荐标题"
    )
    client = FakeWebFetch(task_db, {url: article("2026-08-01", content=noisy_body)})

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.SUCCEEDED
    expected_clean = "新华社北京8月1日电 中共中央政治局召开会议。" + "重要部署。" * 30
    expected_hash = hashlib.sha256(expected_clean.encode("utf-8")).hexdigest()
    with task_db() as db:
        policy = db.scalar(select(Policy))
        assert policy is not None
        assert policy.content_text == expected_clean
        assert policy.content_hash == expected_hash
        assert "来源：新华社" not in policy.content_text
        assert "阅读下一篇" not in policy.content_text


def test_runner_persists_paragraph_structured_body_recovered_from_html(task_db) -> None:
    url = "https://www.news.cn/20260801/seed.html"
    task_id, _ = catalog(task_db, seed_urls=[(url, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    paras = [
        "新华社北京8月1日电 中共中央政治局召开会议。",
        "会议分析研究当前经济形势。",
        "会议还研究了其他事项。",
    ] + ["重要部署。"] * 5
    # extract_article returns the WebFetch generic.article flattened body (single line, no newlines).
    flat_content = "".join(paras)
    client = FakeWebFetch(task_db, {url: article("2026-08-01", content=flat_content)})
    # fetch_text returns the raw HTML with intact <p> structure plus page chrome.
    paragraph_html = (
        "<html><body>新华网 > > 正文 2026 08/01 10:00:00 来源：新华社"
        '<div id="detail">'
        + "".join(f"<p>{p}</p>" for p in paras)
        + "</div>策划：孙承斌 新华通讯社出品 阅读下一篇： 37 其他推荐</body></html>"
    )

    def fetch_text(requested_url: str) -> str:
        if requested_url == url:
            return paragraph_html
        return "<rss><channel></channel></rss>"  # discovery: no extra candidates

    client.fetch_text = fetch_text

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.SUCCEEDED
    expected_body = "\n".join(paras)
    expected_hash = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
    with task_db() as db:
        policy = db.scalar(select(Policy))
        assert policy is not None
        # The stored body is the paragraph-structured HTML body, not the flattened content.
        assert policy.content_text == expected_body
        assert policy.content_hash == expected_hash
        assert "\n" in policy.content_text
        for chrome in ("新华网 >", "来源：", "策划：", "新华通讯社出品", "阅读下一篇"):
            assert chrome not in policy.content_text


def test_runner_falls_back_to_flat_content_when_paragraph_fetch_fails(task_db) -> None:
    url = "https://www.news.cn/20260801/seed.html"
    task_id, _ = catalog(task_db, seed_urls=[(url, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    noisy_body = (
        "2026年8月1日 10:00:00 来源：新华社 "
        "新华社北京8月1日电 中共中央政治局召开会议。" + "重要部署。" * 30 + "\n阅读下一篇： 37 其他推荐标题"
    )
    client = FakeWebFetch(task_db, {url: article("2026-08-01", content=noisy_body)})

    def fetch_text(requested_url: str) -> str:
        if requested_url == url:
            raise WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="段落抓取失败", retryable=True)
        return "<rss><channel></channel></rss>"  # discovery succeeds with no extra candidates

    client.fetch_text = fetch_text

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.SUCCEEDED
    expected_clean = "新华社北京8月1日电 中共中央政治局召开会议。" + "重要部署。" * 30
    expected_hash = hashlib.sha256(expected_clean.encode("utf-8")).hexdigest()
    with task_db() as db:
        policy = db.scalar(select(Policy))
        assert policy is not None
        assert policy.content_text == expected_clean
        assert policy.content_hash == expected_hash
        logs = list(db.scalars(select(CrawlTaskLog).where(CrawlTaskLog.task_id == task_id)))
        assert any("回退扁平正文" in log.message for log in logs)


def test_runner_verified_seed_failure_overrides_partial_success_and_preserves_seed_identity(task_db) -> None:
    seed = "https://www.news.cn/20260801/seed.html"
    other = "https://www.news.cn/20260730/other.html"
    task_id, _ = catalog(task_db, seed_urls=[(seed, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    client = FakeWebFetch(
        task_db, {seed: article("2026-08-01", title="标题不一致"), other: article("2026-07-30")}
    )
    client.fetch_text = lambda _url: (
        f'<a href="{seed}">中共中央政治局召开会议</a><a href="{other}">中共中央政治局召开会议</a>'
    )

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.FAILED
    with task_db() as db:
        task = db.get(CrawlTask, task_id)
        items = list(
            db.scalars(
                select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id).order_by(CrawlTaskItem.id)
            )
        )
        assert [item.status for item in items] == ["failed", "stored"]
        assert items[0].reason_code == "SEED_TITLE_MISMATCH"
        assert (task.success_count, task.failed_count) == (1, 1)
        assert task.error_summary == "已核验历史种子验收失败。"


def test_runner_treats_verified_seed_classification_rejection_as_failure(task_db) -> None:
    seed = "https://www.news.cn/20260801/seed.html"
    task_id, _ = catalog(
        task_db,
        seed_urls=[(seed, True, "中共中央政治局召开会议", date(2026, 8, 1))],
    )
    rejected = article("2026-08-01", content="新华社北京8月1日电 这不是会议导语。" + "内容。" * 30)

    result = runner(task_db, FakeWebFetch(task_db, {seed: rejected})).run(task_id)

    assert result.status is TaskStatus.FAILED
    with task_db() as db:
        item = db.scalar(select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id))
        assert item.status == "failed"
        assert item.reason_code == "SEED_LEAD_NOT_MATCHED"


def test_runner_continues_after_failure_and_summarizes_mixed_results(task_db) -> None:
    failed = "https://www.news.cn/20260729/fail.html"
    filtered = "https://www.news.cn/20260730/filter.html"
    stored = "https://www.news.cn/20260731/store.html"
    task_id, _ = catalog(task_db, seed_urls=[])
    client = FakeWebFetch(
        task_db,
        {
            failed: WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="safe", retryable=True),
            filtered: article("2026-07-30", title="其他标题"),
            stored: article("2026-07-31"),
        },
    )
    client.fetch_text = lambda _url: "".join(
        f'<a href="{url}">中共中央政治局召开会议</a>' for url in (failed, filtered, stored)
    )

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.PARTIALLY_SUCCEEDED
    with task_db() as db:
        task = db.get(CrawlTask, task_id)
        assert (task.discovered_count, task.success_count, task.filtered_count, task.failed_count) == (
            3,
            1,
            1,
            1,
        )
        assert [
            item.status
            for item in db.scalars(
                select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id).order_by(CrawlTaskItem.id)
            )
        ] == ["failed", "filtered", "stored"]


def test_runner_honors_pending_and_between_candidate_cancellation(task_db) -> None:
    url = "https://www.news.cn/20260801/item.html"
    task_id, _ = catalog(task_db, seed_urls=[(url, True, "中共中央政治局召开会议", date(2026, 8, 1))])
    TaskRepository(task_db).request_cancel(task_id, NOW)
    client = FakeWebFetch(task_db, {url: article("2026-08-01")})
    assert runner(task_db, client).run(task_id).status is TaskStatus.CANCELLED
    assert client.calls == []

    first = "https://www.news.cn/20260730/first.html"
    second = "https://www.news.cn/20260731/second.html"
    with task_db.begin() as db:
        running_task = CrawlTask(
            rule_id=1, trigger_type="manual", status="pending", request_snapshot_json="{}"
        )
        db.add(running_task)
        db.flush()
        running_id = running_task.id
    running_client = FakeWebFetch(
        task_db,
        {url: article("2026-08-01"), first: article("2026-07-30"), second: article("2026-07-31")},
    )
    running_client.fetch_text = lambda _url: (
        f'<a href="{first}">中共中央政治局召开会议</a><a href="{second}">中共中央政治局召开会议</a>'
    )
    running_client.after_extract = lambda _url: TaskRepository(task_db).request_cancel(running_id, NOW)

    assert runner(task_db, running_client).run(running_id).status is TaskStatus.CANCELLED
    with task_db() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(CrawlTaskItem).where(CrawlTaskItem.task_id == running_id)
            )
            == 1
        )


def test_runner_marks_cross_url_same_content_duplicate(task_db) -> None:
    first = "https://www.news.cn/20260730/first.html"
    second = "https://www.news.cn/20260730/second.html"
    task_id, _ = catalog(task_db, seed_urls=[])
    shared = article("2026-07-30")
    client = FakeWebFetch(task_db, {first: shared, second: shared})
    client.fetch_text = lambda _url: (
        f'<a href="{first}">中共中央政治局召开会议</a><a href="{second}">中共中央政治局召开会议</a>'
    )

    assert runner(task_db, client).run(task_id).status is TaskStatus.SUCCEEDED
    with task_db() as db:
        task = db.get(CrawlTask, task_id)
        assert (task.success_count, task.duplicate_count) == (1, 1)
        assert [
            item.status
            for item in db.scalars(
                select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id).order_by(CrawlTaskItem.id)
            )
        ] == ["stored", "duplicate"]


def test_policy_and_item_completion_are_atomic_on_item_update_failure(task_db) -> None:
    url = "https://www.news.cn/20260730/atomic.html"
    task_id, _ = catalog(task_db, seed_urls=[])
    client = FakeWebFetch(task_db, {url: article("2026-07-30")})
    client.fetch_text = lambda _url: f'<a href="{url}">中共中央政治局召开会议</a>'
    with task_db.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER abort_stored_item BEFORE UPDATE ON crawl_task_items "
                "WHEN NEW.status = 'stored' BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
        )

    result = runner(task_db, client).run(task_id)

    assert result.status is TaskStatus.FAILED
    with task_db() as db:
        assert db.scalar(select(func.count()).select_from(Policy)) == 0
        item = db.scalar(select(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id))
        assert item.status == "failed"
        assert item.reason_code == "POLICY_WRITE_FAILED"
        assert item.finished_at is not None


def test_concurrent_runners_same_content_store_once_and_duplicate_once(task_db) -> None:
    first_url = "https://www.news.cn/20260730/concurrent-a.html"
    second_url = "https://www.news.cn/20260730/concurrent-b.html"
    first_id, _ = catalog(task_db, seed_urls=[])
    with task_db.begin() as db:
        second_task = CrawlTask(
            rule_id=1, trigger_type="manual", status="pending", request_snapshot_json="{}"
        )
        db.add(second_task)
        db.flush()
        second_id = second_task.id
    shared_article = article("2026-07-30")
    first_client = FakeWebFetch(task_db, {first_url: shared_article})
    second_client = FakeWebFetch(task_db, {second_url: shared_article})
    first_client.fetch_text = lambda _url: f'<a href="{first_url}">中共中央政治局召开会议</a>'
    second_client.fetch_text = lambda _url: f'<a href="{second_url}">中共中央政治局召开会议</a>'
    barrier = Barrier(2)
    first_client.after_extract = lambda _url: barrier.wait(timeout=5)
    second_client.after_extract = lambda _url: barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: runner(task_db, pair[1]).run(pair[0]),
                [(first_id, first_client), (second_id, second_client)],
            )
        )

    assert [result.status for result in results] == [TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED]
    with task_db() as db:
        assert db.scalar(select(func.count()).select_from(Policy)) == 1
        statuses = list(db.scalars(select(CrawlTaskItem.status).order_by(CrawlTaskItem.id)))
        assert sorted(statuses) == ["duplicate", "stored"]


def test_runner_updates_policy_and_revision_references_real_second_task_item(task_db) -> None:
    url = "https://www.news.cn/20260730/revision.html"
    first_id, _ = catalog(task_db, seed_urls=[])
    first_client = FakeWebFetch(task_db, {url: article("2026-07-30")})
    first_client.fetch_text = lambda _url: f'<a href="{url}">中共中央政治局召开会议</a>'
    assert runner(task_db, first_client).run(first_id).status is TaskStatus.SUCCEEDED
    with task_db.begin() as db:
        second = CrawlTask(rule_id=1, trigger_type="manual", status="pending", request_snapshot_json="{}")
        db.add(second)
        db.flush()
        second_id = second.id
    changed = article(
        "2026-07-30",
        content="新华社北京7月30日电 中共中央政治局召开会议。" + "新部署。" * 40,
    )
    second_client = FakeWebFetch(task_db, {url: changed})
    second_client.fetch_text = first_client.fetch_text

    assert runner(task_db, second_client).run(second_id).status is TaskStatus.SUCCEEDED
    with task_db() as db:
        item = db.scalar(select(CrawlTaskItem).where(CrawlTaskItem.task_id == second_id))
        revision = db.scalar(select(PolicyRevision))
        assert item.status == "updated"
        assert revision.task_item_id == item.id


def test_real_rss_relative_url_discovery_and_partial_entry_failure(task_db) -> None:
    relative = "/20260730/rss.html"
    absolute = "https://www.news.cn/20260730/rss.html"
    task_id, rule_id = catalog(task_db, seed_urls=[])
    with task_db.begin() as db:
        rule = db.get(CollectionRule, rule_id)
        rule.discovery_config_json = (
            '{"rss_urls":["https://www.news.cn/feed.xml"],"channel_urls":["https://www.news.cn/broken"]}'
        )
    client = FakeWebFetch(task_db, {absolute: article("2026-07-30")})

    def discover(url: str) -> str:
        if url.endswith("broken"):
            raise WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="safe", retryable=True)
        return (
            "<rss><channel><item><title>中共中央政治局召开会议</title>"
            f"<link>{relative}</link></item></channel></rss>"
        )

    client.fetch_text = discover
    assert runner(task_db, client).run(task_id).status is TaskStatus.PARTIALLY_SUCCEEDED
    assert absolute in client.calls


def test_bad_unknown_snapshot_fails_safely_without_external_request(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    with task_db.begin() as db:
        db.get(CrawlTask, task_id).request_snapshot_json = '{"version":2}'
    client = FakeWebFetch(task_db, {})

    assert runner(task_db, client).run(task_id).status is TaskStatus.FAILED
    assert client.calls == []


@pytest.mark.parametrize("inactive", ["rule", "source", "category"])
def test_inactive_configuration_fails_without_external_request(task_db, inactive: str) -> None:
    task_id, rule_id = catalog(task_db, seed_urls=[])
    with task_db.begin() as db:
        rule = db.get(CollectionRule, rule_id)
        target = rule if inactive == "rule" else getattr(rule, inactive)
        target.is_active = False
    client = FakeWebFetch(task_db, {})
    assert runner(task_db, client).run(task_id).status is TaskStatus.FAILED
    assert client.calls == []


def test_all_ordinary_failures_fail_and_all_filtered_candidates_succeed(task_db) -> None:
    failed_url = "https://www.news.cn/20260730/failed.html"
    failed_id, _ = catalog(task_db, seed_urls=[])
    failed_client = FakeWebFetch(
        task_db,
        {failed_url: WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="safe", retryable=True)},
    )
    failed_client.fetch_text = lambda _url: f'<a href="{failed_url}">中共中央政治局召开会议</a>'
    assert runner(task_db, failed_client).run(failed_id).status is TaskStatus.FAILED

    with task_db.begin() as db:
        filtered_task = CrawlTask(
            rule_id=1, trigger_type="manual", status="pending", request_snapshot_json="{}"
        )
        db.add(filtered_task)
        db.flush()
        filtered_id = filtered_task.id
    filtered_url = "https://www.news.cn/20260730/filtered.html"
    filtered_client = FakeWebFetch(task_db, {filtered_url: article("2026-07-30", title="其他标题")})
    filtered_client.fetch_text = lambda _url: f'<a href="{filtered_url}">中共中央政治局召开会议</a>'
    assert runner(task_db, filtered_client).run(filtered_id).status is TaskStatus.SUCCEEDED


def test_claim_rejects_already_running_task_with_stable_error(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db)
    assert repository.claim(task_id, NOW) is TaskStatus.RUNNING
    with pytest.raises(TaskRepositoryError) as raised:
        repository.claim(task_id, NOW)
    assert getattr(raised.value, "code", None) == "TASK_ALREADY_CLAIMED"


def test_runner_rejects_repeat_run_and_sanitizes_recursive_log_context(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db, secrets=("super-secret",))
    repository.add_log(
        task_id,
        "error",
        "安全日志",
        {
            "Authorization": "Bearer super-secret",
            "nested": {"password": "pwd", "url": "https://x/?token=abc"},
            "path": "/Users/private/file",
        },
    )
    client = FakeWebFetch(task_db, {})
    assert runner(task_db, client).run(task_id).status is TaskStatus.SUCCEEDED
    assert runner(task_db, client).run(task_id).status is TaskStatus.SUCCEEDED
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
        assert (
            "super-secret" not in raw and "pwd" not in raw and "token=abc" not in raw and "/Users/" not in raw
        )
        assert "[REDACTED]" in raw


def test_log_sanitizer_handles_key_variants_paths_limits_and_strict_json(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db, secrets=("abc", "abcdef", ""))
    deep: object = "bottom-secret"
    for _ in range(30):
        deep = {"safe": deep}
    repository.add_log(
        task_id,
        "warning",
        "control\x00value abcdef C:\\Users\\Jane Doe\\secret.txt",
        {
            "Set-Cookie": "sid=cookie-value",
            "session.id": "session-value",
            "private-key": "key-value",
            "credential": "credential-value",
            "url": "https://example.test/a?access-token=query-secret&ok=visible",
            "posix": "read /Users/Jane Doe/private file.txt now",
            "unc": r"read \\server\share\Private File now",
            "nan": math.nan,
            "inf": math.inf,
            "deep": deep,
            "long": "x" * 100_000,
        },
    )
    with task_db() as db:
        log = db.scalar(
            select(CrawlTaskLog).where(CrawlTaskLog.task_id == task_id).order_by(CrawlTaskLog.id.desc())
        )
        parsed = json.loads(
            log.context_json,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        serialized = json.dumps(parsed, ensure_ascii=False)
        for secret in (
            "abcdef",
            "query-secret",
            "cookie-value",
            "session-value",
            "credential-value",
            "key-value",
            "Jane Doe",
            "bottom-secret",
        ):
            assert secret not in serialized and secret not in log.message
        assert "example.test" in serialized
        assert "visible" in serialized
        assert "\x00" not in log.message
        assert len(log.context_json) < 20_000


def test_sanitizer_covers_prefixed_keys_mapping_keys_truncation_and_arbitrary_paths(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    secret = "loaded-secret-value"
    repository = TaskRepository(task_db, secrets=(secret,))
    context = {
        key: "must-not-leak"
        for key in (
            "db_password",
            "client_secret",
            "user_session",
            "my_authorization",
            "cookie_value",
            "api_key_value",
        )
    }
    context.update(
        {
            f"key-{secret}": "audit-one",
            "key-[REDACTED]": "audit-two",
            "query": "https://example.test/?db_password=q1&client_secret=q2&user_session=q3",
            "boundary": "x" * 4090 + secret,
            "paths": [
                "/root/private file",
                "/srv/app/config",
                "/mnt/share/a",
                "/data/a",
                "/usr/local/a",
                "/Volumes/My Disk/a",
                "/custom path/secret file",
            ],
        }
    )
    repository.add_log(task_id, "info", "safe", context)
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
    for leaked in ("must-not-leak", secret, "q1", "q2", "q3", "/root", "/srv", "/custom path"):
        assert leaked not in raw
    assert "example.test" in raw
    parsed = json.loads(raw)
    assert len(parsed) == len(context)


def test_snapshot_rejects_sensitive_keys_values_queries_and_invalid_bounds(task_db) -> None:
    _task_id, rule_id = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db, secrets=("loaded-secret",))
    invalid_requests = [
        {"db_password": "value"},
        {"client_secret": "value"},
        {"user_session": "value"},
        {"my_authorization": "value"},
        {"cookie_value": "value"},
        {"api_key_value": "value"},
        {"safe": "loaded-secret"},
        {"url": "https://example.test/?access_token=value"},
        {"nan": math.nan},
        {"long": "x" * 5000},
    ]
    for request in invalid_requests:
        with pytest.raises(TaskRepositoryError) as raised:
            repository.create_task(rule_id, "manual", request, NOW)
        assert raised.value.code == "TASK_SNAPSHOT_INVALID"


def test_path_sanitizer_handles_delimiters_without_redacting_web_urls(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    TaskRepository(task_db).add_log(
        task_id,
        "info",
        "paths",
        {
            "local": [
                "path=/root/foo",
                "cwd:/srv/app",
                "file=/custom path/key",
                "(/mnt/share/key)",
                "'/data/private key'",
                '"/Volumes/My Disk/key"',
            ],
            "web": [
                "http://host/path",
                "https://host/path/to/file",
                "//cdn.example.test/path/file",
            ],
        },
    )
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
    for local in ("/root", "/srv", "/custom path", "/mnt", "/data", "/Volumes"):
        assert local not in raw
    for web in ("http://host/path", "https://host/path/to/file", "//cdn.example.test/path/file"):
        assert web in raw


@pytest.mark.parametrize("location", ["discovery", "seed_url", "seed_title"])
def test_snapshot_rejects_sensitive_data_anywhere_in_frozen_payload(task_db, location: str) -> None:
    _task_id, rule_id = catalog(
        task_db,
        seed_urls=[
            (
                "https://www.news.cn/20260801/loaded-secret.html"
                if location == "seed_url"
                else "https://www.news.cn/20260801/safe.html",
                True,
                "loaded-secret" if location == "seed_title" else "中共中央政治局召开会议",
                date(2026, 8, 1),
            )
        ],
    )
    if location == "discovery":
        with task_db.begin() as db:
            db.get(CollectionRule, rule_id).discovery_config_json = (
                '{"rss_urls":[],"channel_urls":['
                '"https://www.news.cn/channel?client_secret=value&api_key=value&token=value"]}'
            )
    repository = TaskRepository(task_db, secrets=("loaded-secret",))
    before = repository.get(1)

    with pytest.raises(TaskRepositoryError) as raised:
        repository.create_task(rule_id, "manual", {"url": "https://example.test/normal/path"}, NOW)

    assert raised.value.code == "TASK_SNAPSHOT_INVALID"
    with task_db() as db:
        assert db.scalar(select(func.count()).select_from(CrawlTask)) == 1
    assert before is not None


@pytest.mark.parametrize("encoded_key", ["api%5Fkey", "API%255fKEY", "api%ZZkey"])
def test_percent_encoded_sensitive_query_keys_are_safe_in_logs_and_snapshots(
    task_db, encoded_key: str
) -> None:
    task_id, rule_id = catalog(task_db, seed_urls=[])
    repository = TaskRepository(task_db)
    url = f"https://example.test/path?{encoded_key}=LEAK&ok=visible"
    repository.add_log(task_id, "info", "query", {"url": url})
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
    assert "LEAK" not in raw
    assert "visible" in raw
    with pytest.raises(TaskRepositoryError) as raised:
        repository.create_task(rule_id, "manual", {"url": url}, NOW)
    assert raised.value.code == "TASK_SNAPSHOT_INVALID"


def test_url_path_redaction_has_no_placeholder_collision(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    value = "WEBURLTOKEN0 /root/private https://example.test/WEBURLTOKEN1/path WEBURLTOKEN1 cwd:/srv/app"
    TaskRepository(task_db).add_log(task_id, "info", "paths", {"value": value})
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
    assert "WEBURLTOKEN0" in raw
    assert "https://example.test/WEBURLTOKEN1/path" in raw
    assert "/root" not in raw and "/srv" not in raw


def test_path_redaction_preserves_space_before_following_url(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    value = "WEBURLTOKEN0 /root/a https://x/WEBURLTOKEN1/a"
    TaskRepository(task_db).add_log(task_id, "info", "paths", {"value": value})
    with task_db() as db:
        raw = db.scalar(select(CrawlTaskLog.context_json).where(CrawlTaskLog.task_id == task_id))
    assert "WEBURLTOKEN0 [REDACTED] https://x/WEBURLTOKEN1/a" in raw
    assert "/root/a" not in raw


def test_all_discovery_failures_fail_task_but_successful_empty_discovery_succeeds(task_db) -> None:
    task_id, _ = catalog(task_db, seed_urls=[])
    failed_client = FakeWebFetch(task_db, {})
    failed_client.fetch_text = lambda _url: (_ for _ in ()).throw(
        WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="unsafe", retryable=True)
    )
    assert runner(task_db, failed_client).run(task_id).status is TaskStatus.FAILED

    with task_db.begin() as db:
        empty_task = CrawlTask(rule_id=1, trigger_type="manual", status="pending", request_snapshot_json="{}")
        db.add(empty_task)
        db.flush()
        empty_id = empty_task.id
    assert runner(task_db, FakeWebFetch(task_db, {})).run(empty_id).status is TaskStatus.SUCCEEDED


def test_last_candidate_cancellation_keeps_discovered_total(task_db) -> None:
    urls = [f"https://www.news.cn/202607{i:02d}/item.html" for i in range(20, 30)]
    task_id, _ = catalog(task_db, seed_urls=[])
    client = FakeWebFetch(task_db, {url: article("2026-07-20") for url in urls})
    client.fetch_text = lambda _url: "".join(f'<a href="{url}">中共中央政治局召开会议</a>' for url in urls)
    client.after_extract = lambda _url: TaskRepository(task_db).request_cancel(task_id, NOW)

    assert runner(task_db, client).run(task_id).status is TaskStatus.CANCELLED
    with task_db() as db:
        task = db.get(CrawlTask, task_id)
        assert task.discovered_count == 10
        assert (
            db.scalar(select(func.count()).select_from(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id))
            == 1
        )


def test_beijing_window_starts_at_midnight(task_db) -> None:
    boundary = "https://www.news.cn/20210801/boundary.html"
    old = "https://www.news.cn/20210731/old.html"
    task_id, _ = catalog(
        task_db,
        seed_urls=[
            (boundary, True, "中共中央政治局召开会议", date(2021, 8, 1)),
            (old, True, "中共中央政治局召开会议", date(2021, 7, 31)),
        ],
    )
    client = FakeWebFetch(task_db, {boundary: article("2021-08-01")})
    assert runner(task_db, client).run(task_id).status is TaskStatus.SUCCEEDED
    assert boundary in client.calls and old not in client.calls
