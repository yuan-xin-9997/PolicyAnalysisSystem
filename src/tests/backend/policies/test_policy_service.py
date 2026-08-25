from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.policies.models import Policy, PolicyRevision
from policy_analysis.policies.schemas import PolicyQuery, PolicyWrite
from policy_analysis.policies.service import PolicyService, PolicyWriteError
from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem
from pydantic import ValidationError
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def migrated_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, sessionmaker[Session], Path]]:
    database_path = tmp_path / "policies.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(database_path))
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    engine = build_engine(database_path)
    try:
        yield engine, session_factory(engine), database_path
    finally:
        engine.dispose()


@pytest.fixture
def policy_catalog(
    migrated_database: tuple[Engine, sessionmaker[Session], Path],
) -> tuple[sessionmaker[Session], int, int, int, tuple[int, ...]]:
    _engine, sessions, _database_path = migrated_database
    now = datetime(2026, 8, 1, 8, tzinfo=SHANGHAI)
    with sessions.begin() as database:
        source = Source(
            code="xinhua",
            name="新华网",
            organization="新华社",
            base_url="https://www.news.cn/",
            adapter_type="xinhua",
            allowed_domains_json='["news.cn"]',
            is_active=True,
        )
        meeting = PolicyCategory(
            code="politburo_meeting",
            name="中央政治局会议",
            description=None,
            is_active=True,
        )
        economy = PolicyCategory(
            code="economy",
            name="经济工作",
            description=None,
            is_active=True,
        )
        database.add_all([source, meeting, economy])
        database.flush()
        rule = CollectionRule(
            source_id=source.id,
            category_id=meeting.id,
            name="中央政治局会议",
            include_keywords_json='["中共中央政治局召开会议"]',
            exclude_keywords_json="[]",
            history_years=5,
            discovery_config_json='{"rss_urls":["https://www.news.cn/rss.xml"]}',
            is_active=True,
            created_at=now,
            updated_at=now,
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
                candidate_url=f"https://www.news.cn/politics/2026073{index}/item.html",
                status="stored",
            )
            for index in range(1, 6)
        ]
        database.add_all(items)
        database.flush()
        return sessions, source.id, meeting.id, economy.id, tuple(item.id for item in items)


def article_record(
    source_id: int,
    category_id: int,
    *,
    title: str = "中共中央政治局召开会议 研究经济工作",
    canonical_url: str = "https://www.news.cn/politics/20260730/example.html",
    publisher: str = "新华社",
    published_at: datetime = datetime(2026, 7, 30, 14, tzinfo=SHANGHAI),
    content_text: str = "会议分析研究当前经济形势，部署下半年经济工作。",
    webfetch_artifact_id: str = "artifact-policy-1",
    crawled_at: datetime = datetime(2026, 7, 31, 12, tzinfo=SHANGHAI),
) -> PolicyWrite:
    return PolicyWrite(
        source_id=source_id,
        category_id=category_id,
        title=title,
        canonical_url=canonical_url,
        publisher=publisher,
        published_at=published_at,
        content_text=content_text,
        content_hash=hashlib.sha256(content_text.encode()).hexdigest(),
        webfetch_artifact_id=webfetch_artifact_id,
        crawled_at=crawled_at,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", True),
        ("category_id", "1"),
        ("title", 123),
        ("title", " 有前导空格"),
        ("canonical_url", " https://www.news.cn/politics/example.html"),
        ("canonical_url", "https://user:secret@www.news.cn/politics/example.html"),
        ("canonical_url", "https://www.news.cn/politics/example.html#fragment"),
        ("canonical_url", "https://WWW.NEWS.CN/politics/example.html"),
        ("canonical_url", "https://www.news.cn"),
        ("canonical_url", "https://www.news.cn:bad/politics/example.html"),
        ("publisher", "新华社 "),
        ("content_text", "   "),
        ("content_hash", " hash-value"),
        ("content_hash", "hash\nvalue"),
        ("webfetch_artifact_id", "artifact\tvalue"),
        ("webfetch_artifact_id", "artifact\x00hidden"),
        ("published_at", datetime(2026, 7, 30, 14)),
        ("crawled_at", "2026-07-31T12:00:00+08:00"),
    ],
)
def test_policy_write_strictly_rejects_ambiguous_or_coerced_values(field: str, value: object) -> None:
    payload = article_record(1, 1).model_dump(mode="python")
    payload["canonical_url"] = str(payload["canonical_url"])
    payload[field] = value

    with pytest.raises(ValidationError):
        PolicyWrite.model_validate(payload)


def test_policy_write_forbids_extra_fields() -> None:
    payload = article_record(1, 1).model_dump(mode="python")
    payload["canonical_url"] = str(payload["canonical_url"])
    payload["unexpected"] = "secret"

    with pytest.raises(ValidationError):
        PolicyWrite.model_validate(payload)


@pytest.mark.parametrize("field", ["title", "publisher", "content_hash", "webfetch_artifact_id"])
@pytest.mark.parametrize("unsafe_character", ["\u0085", "\u202e", "\u2066", "\u2069", "\u200b"])
def test_policy_write_metadata_rejects_all_control_and_format_characters(
    field: str,
    unsafe_character: str,
) -> None:
    payload = article_record(1, 1).model_dump(mode="python")
    payload["canonical_url"] = str(payload["canonical_url"])
    payload[field] = f"safe{unsafe_character}value"

    with pytest.raises(ValidationError):
        PolicyWrite.model_validate(payload)


def test_policy_write_content_allows_normal_line_breaks_and_tabs() -> None:
    content = "第一行\n第二行\r\n\t缩进"

    record = article_record(1, 1, content_text=content)

    assert record.content_text == content


@pytest.mark.parametrize(
    "unsafe_character",
    ["\x00", "\u0085", "\u202e", "\u2066", "\u2069", "\u200b"],
)
def test_policy_write_content_rejects_other_control_and_all_format_characters(
    unsafe_character: str,
) -> None:
    payload = article_record(1, 1).model_dump(mode="python")
    payload["canonical_url"] = str(payload["canonical_url"])
    payload["content_text"] = f"正文{unsafe_character}内容"

    with pytest.raises(ValidationError):
        PolicyWrite.model_validate(payload)


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        hashlib.sha256(b"different-content").hexdigest(),
    ],
)
def test_policy_write_requires_exact_lowercase_sha256_of_content(invalid_hash: str) -> None:
    payload = article_record(1, 1).model_dump(mode="python")
    payload["canonical_url"] = str(payload["canonical_url"])
    payload["content_hash"] = invalid_hash

    with pytest.raises(ValidationError):
        PolicyWrite.model_validate(payload)


@pytest.mark.parametrize("construction", ["model_copy", "model_construct"])
def test_forged_same_hash_for_different_content_is_rejected_before_deduplication(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
    construction: str,
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    original = article_record(source_id, category_id)
    stored = service.upsert(original, task_item_id=item_ids[0])
    updates = {
        "canonical_url": "https://www.news.cn/politics/20260730/forged.html",
        "content_text": "完全不同的伪造正文。",
    }
    if construction == "model_copy":
        forged = original.model_copy(update=updates)
    else:
        forged_payload = original.model_dump(mode="python")
        forged_payload.update(updates)
        forged = PolicyWrite.model_construct(**forged_payload)

    with pytest.raises(PolicyWriteError) as raised:
        service.upsert(forged, task_item_id=item_ids[1])

    assert raised.value.code == "POLICY_WRITE_INVALID"
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(Policy)) == 1
        assert database.get(Policy, stored.policy_id).content_text == original.content_text  # type: ignore[union-attr]


def test_policy_query_bounds_page_before_building_sqlite_offset() -> None:
    assert PolicyQuery(page=1_000_000, page_size=1).page == 1_000_000
    assert PolicyQuery(page=1, page_size=10_000).page_size == 10_000

    with pytest.raises(ValidationError):
        PolicyQuery(page=1_000_001, page_size=1)
    with pytest.raises(ValidationError):
        PolicyQuery(page=10**100, page_size=1)
    with pytest.raises(ValidationError):
        PolicyQuery(page=1, page_size=10_001)
    with pytest.raises(ValidationError):
        PolicyQuery(page=1, page_size=10**100)


@pytest.mark.parametrize("field", ["keyword", "full_text", "publisher"])
@pytest.mark.parametrize("unsafe_character", ["\u0085", "\u202e", "\u2066", "\u2069", "\u200b"])
def test_policy_query_text_rejects_all_control_and_format_characters(
    field: str,
    unsafe_character: str,
) -> None:
    with pytest.raises(ValidationError):
        PolicyQuery(**{field: f"政策{unsafe_character}查询"})


def test_policy_service_accepts_only_policy_write_and_positive_task_item(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    record = article_record(source_id, category_id)

    with pytest.raises(TypeError, match="PolicyWrite"):
        service.upsert(record.model_dump(), task_item_id=item_ids[0])  # type: ignore[arg-type]
    for invalid_task_item in (0, -1, True, "1"):
        with pytest.raises(ValueError, match="positive integer"):
            service.upsert(record, task_item_id=invalid_task_item)  # type: ignore[arg-type]


def test_policy_service_revalidates_model_copy_updates_before_database_write(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    invalid = article_record(source_id, category_id).model_copy(update={"content_hash": "invalid\nhash"})

    with pytest.raises(PolicyWriteError, match="政策写入数据无效") as raised:
        PolicyService(sessions).upsert(invalid, task_item_id=item_ids[0])

    assert raised.value.code == "POLICY_WRITE_INVALID"
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(Policy)) == 0


def test_upsert_stores_new_policy_with_utc_times(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    record = article_record(source_id, category_id)

    result = service.upsert(record, task_item_id=item_ids[0])

    assert result.outcome == "stored"
    with sessions() as database:
        stored = database.get(Policy, result.policy_id)
        assert stored is not None
        assert stored.canonical_url == str(record.canonical_url)
        assert stored.first_crawled_at == record.crawled_at.astimezone(UTC)
        assert stored.last_crawled_at == record.crawled_at.astimezone(UTC)
        assert stored.published_at == record.published_at.astimezone(UTC)
        assert database.scalar(select(func.count()).select_from(PolicyRevision)) == 0


def test_same_hash_duplicate_only_advances_last_crawled_at_and_never_regresses(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    original = article_record(source_id, category_id)
    stored = service.upsert(original, task_item_id=item_ids[0])
    later = original.model_copy(
        update={
            "title": "同哈希不应覆盖标题",
            "publisher": "同哈希不应覆盖发布者",
            "crawled_at": original.crawled_at + timedelta(hours=2),
        }
    )

    duplicate = service.upsert(later, task_item_id=item_ids[1])
    older = service.upsert(
        original.model_copy(update={"crawled_at": original.crawled_at - timedelta(days=1)}),
        task_item_id=item_ids[2],
    )

    assert duplicate.outcome == older.outcome == "duplicate"
    assert duplicate.policy_id == older.policy_id == stored.policy_id
    with sessions() as database:
        policy = database.get(Policy, stored.policy_id)
        assert policy is not None
        assert policy.title == original.title
        assert policy.publisher == original.publisher
        assert policy.last_crawled_at == later.crawled_at.astimezone(UTC)
        assert database.scalar(select(func.count()).select_from(PolicyRevision)) == 0


def test_same_source_hash_across_urls_is_one_duplicate_policy(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    record = article_record(source_id, category_id)
    first = service.upsert(record, task_item_id=item_ids[0])

    duplicate = service.upsert(
        record.model_copy(
            update={
                "canonical_url": "https://www.news.cn/politics/20260730/reprint.html",
                "crawled_at": record.crawled_at + timedelta(hours=1),
            }
        ),
        task_item_id=item_ids[1],
    )

    assert duplicate.outcome == "duplicate"
    assert duplicate.policy_id == first.policy_id
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(Policy)) == 1
        policy = database.get(Policy, first.policy_id)
        assert policy is not None
        assert policy.canonical_url == str(record.canonical_url)


def test_single_process_concurrent_writes_cannot_create_cross_url_hash_duplicates(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    original = article_record(source_id, category_id)
    reprint = original.model_copy(
        update={"canonical_url": "https://www.news.cn/politics/20260730/concurrent-reprint.html"}
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(PolicyService(sessions).upsert, record, item_id)
            for record, item_id in zip((original, reprint), item_ids[:2], strict=True)
        ]
        results = [future.result() for future in futures]

    assert sorted(result.outcome for result in results) == ["duplicate", "stored"]
    assert len({result.policy_id for result in results}) == 1
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(Policy)) == 1


def test_changed_url_content_preserves_revision_before_updating_all_current_fields(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, economy_id, item_ids = policy_catalog
    fixed_now = datetime(2026, 8, 1, 9, tzinfo=SHANGHAI)
    service = PolicyService(sessions, now=lambda: fixed_now)
    original = article_record(source_id, category_id)
    first = service.upsert(original, task_item_id=item_ids[0])
    changed = article_record(
        source_id,
        economy_id,
        title="中共中央政治局召开会议 审议修订稿",
        publisher="新华网",
        published_at=original.published_at + timedelta(hours=1),
        content_text=original.content_text + " 新增内容。",
        webfetch_artifact_id="artifact-policy-2",
        crawled_at=original.crawled_at + timedelta(days=1),
    )

    result = service.upsert(changed, task_item_id=item_ids[2])

    assert result.outcome == "updated"
    assert result.policy_id == first.policy_id
    assert service.revision_count(first.policy_id) == 1
    with sessions() as database:
        policy = database.get(Policy, first.policy_id)
        revision = database.scalar(select(PolicyRevision))
        assert policy is not None and revision is not None
        assert (
            revision.content_text,
            revision.content_hash,
            revision.webfetch_artifact_id,
            revision.task_item_id,
            revision.replaced_at,
        ) == (
            original.content_text,
            original.content_hash,
            original.webfetch_artifact_id,
            item_ids[2],
            fixed_now.astimezone(UTC),
        )
        assert policy.first_crawled_at == original.crawled_at.astimezone(UTC)
        assert policy.category_id == economy_id
        assert policy.title == changed.title
        assert policy.publisher == changed.publisher
        assert policy.published_at == changed.published_at.astimezone(UTC)
        assert policy.content_text == changed.content_text
        assert policy.content_hash == changed.content_hash
        assert policy.webfetch_artifact_id == changed.webfetch_artifact_id
        assert policy.last_crawled_at == changed.crawled_at.astimezone(UTC)


@pytest.mark.parametrize("missing_reference", ["source", "category"])
def test_missing_catalog_reference_rolls_back_with_safe_domain_error(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
    missing_reference: str,
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    payload = article_record(source_id, category_id).model_copy(update={f"{missing_reference}_id": 999_999})

    with pytest.raises(PolicyWriteError, match="政策写入引用无效") as raised:
        PolicyService(sessions).upsert(payload, task_item_id=item_ids[0])

    assert raised.value.code == "POLICY_REFERENCE_INVALID"
    assert "sqlite" not in str(raised.value).lower()
    with sessions() as database:
        assert database.scalar(select(func.count()).select_from(Policy)) == 0
        assert database.scalar(select(func.count()).select_from(PolicyRevision)) == 0


def test_missing_revision_task_item_rolls_back_policy_and_revision(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    original = article_record(source_id, category_id)
    first = service.upsert(original, task_item_id=item_ids[0])
    changed = article_record(source_id, category_id, content_text=original.content_text + " 修订。")

    with pytest.raises(PolicyWriteError) as raised:
        service.upsert(changed, task_item_id=999_999)

    assert raised.value.code == "POLICY_REFERENCE_INVALID"
    with sessions() as database:
        policy = database.get(Policy, first.policy_id)
        assert policy is not None
        assert policy.content_hash == original.content_hash
        assert database.scalar(select(func.count()).select_from(PolicyRevision)) == 0


def test_database_failure_after_revision_insert_rolls_back_every_change(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    original = article_record(source_id, category_id)
    first = service.upsert(original, task_item_id=item_ids[0])
    with sessions.begin() as database:
        database.execute(
            text(
                """
                CREATE TRIGGER reject_policy_content_update
                BEFORE UPDATE OF content_text ON policies
                BEGIN
                    SELECT RAISE(ABORT, 'database-secret-must-not-leak');
                END
                """
            )
        )
    changed = article_record(source_id, category_id, content_text=original.content_text + " 修订。")

    with pytest.raises(PolicyWriteError, match="政策写入发生冲突") as raised:
        service.upsert(changed, task_item_id=item_ids[1])

    assert raised.value.code == "POLICY_WRITE_CONFLICT"
    assert "database-secret-must-not-leak" not in str(raised.value)
    with sessions() as database:
        policy = database.get(Policy, first.policy_id)
        assert policy is not None
        assert policy.content_text == original.content_text
        assert policy.content_hash == original.content_hash
        assert database.scalar(select(func.count()).select_from(PolicyRevision)) == 0


def test_title_and_full_text_search_have_distinct_scopes_and_short_term_fallback(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    first = service.upsert(article_record(source_id, category_id), task_item_id=item_ids[0])
    second = service.upsert(
        article_record(
            source_id,
            category_id,
            canonical_url="https://www.news.cn/politics/20260731/second.html",
            title="中共中央政治局召开会议 审议重要文件",
            content_text="会议强调推动科技创新和高质量发展。",
            webfetch_artifact_id="artifact-policy-2",
        ),
        task_item_id=item_ids[1],
    )

    assert [item.id for item in service.search(PolicyQuery(keyword="经济工作")).items] == [first.policy_id]
    assert service.search(PolicyQuery(keyword="科技创新")).items == []
    assert [item.id for item in service.search(PolicyQuery(full_text="科技创新")).items] == [second.policy_id]
    assert service.search(PolicyQuery(full_text="审议重要文件")).items == []
    assert [item.id for item in service.search(PolicyQuery(keyword="经济")).items] == [first.policy_id]
    assert [item.id for item in service.search(PolicyQuery(full_text="经济")).items] == [first.policy_id]
    combined = service.search(PolicyQuery(keyword="重要文件", full_text="科技创新"))
    assert [item.id for item in combined.items] == [second.policy_id]


@pytest.mark.parametrize("keyword", ['"', "OR", "NEAR(", "*", "%", "_", "\\"])
def test_fts_search_treats_syntax_and_like_metacharacters_as_literal_text(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
    keyword: str,
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    service.upsert(article_record(source_id, category_id), task_item_id=item_ids[0])

    result = service.search(PolicyQuery(full_text=keyword))

    assert result.total == 0
    assert result.items == []


def test_fts_triggers_follow_policy_updates_and_deletes(
    policy_catalog: tuple[sessionmaker[Session], int, int, int, tuple[int, ...]],
) -> None:
    sessions, source_id, category_id, _economy_id, item_ids = policy_catalog
    service = PolicyService(sessions)
    stored = service.upsert(article_record(source_id, category_id), task_item_id=item_ids[0])

    with sessions.begin() as database:
        policy = database.get(Policy, stored.policy_id)
        assert policy is not None
        policy.title = "中共中央政治局召开会议 推进区域协调发展"
        policy.content_text = "修订后的正文聚焦区域协调发展。"

    assert service.search(PolicyQuery(full_text="区域协调")).total == 1
    assert service.search(PolicyQuery(keyword="经济工作")).total == 0

    with sessions.begin() as database:
        policy = database.get(Policy, stored.policy_id)
        assert policy is not None
        database.delete(policy)

    assert service.search(PolicyQuery(full_text="区域协调")).total == 0


def test_policy_fts_migration_round_trips_and_rebuilds_existing_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-roundtrip.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(database_path))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = build_engine(database_path)
    sessions = session_factory(engine)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
            sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'policies_fts'")
            ).scalar_one()
            assert "tokenize='trigram'" in sql
        with sessions.begin() as database:
            source = Source(
                code="migration-source",
                name="迁移来源",
                organization="迁移来源",
                base_url="https://www.news.cn/",
                adapter_type="xinhua",
                allowed_domains_json='["news.cn"]',
                is_active=True,
            )
            category = PolicyCategory(code="migration", name="迁移", is_active=True)
            database.add_all([source, category])
            database.flush()
            database.add(
                Policy(
                    source_id=source.id,
                    category_id=category.id,
                    title="中共中央政治局召开会议 迁移验证",
                    canonical_url="https://www.news.cn/politics/20260730/migration.html",
                    publisher="新华社",
                    published_at=datetime(2026, 7, 30, tzinfo=UTC),
                    content_text="迁移往返需要重建全文索引。",
                    content_hash="migration-hash",
                    webfetch_artifact_id="migration-artifact",
                    first_crawled_at=datetime(2026, 7, 31, tzinfo=UTC),
                    last_crawled_at=datetime(2026, 7, 31, tzinfo=UTC),
                )
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM policies_fts WHERE policies_fts MATCH :query"),
                    {"query": '"迁移验证"'},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0002")
    downgraded = build_engine(database_path)
    try:
        names = set(inspect(downgraded).get_table_names())
        assert "policies" in names
        with downgraded.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
            assert (
                connection.execute(
                    text("SELECT count(*) FROM sqlite_master WHERE name LIKE 'policies_fts%'")
                ).scalar_one()
                == 0
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    upgraded = build_engine(database_path)
    try:
        with upgraded.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
            assert (
                connection.execute(
                    text("SELECT count(*) FROM policies_fts WHERE policies_fts MATCH :query"),
                    {"query": '"迁移验证"'},
                ).scalar_one()
                == 1
            )
    finally:
        upgraded.dispose()


def _add_source(
    sessions: sessionmaker[Session],
    code: str,
    name: str,
    organization: str,
    base_url: str,
    allowed_domains: list[str],
) -> int:
    with sessions.begin() as database:
        source = Source(
            code=code,
            name=name,
            organization=organization,
            base_url=base_url,
            adapter_type=code,
            allowed_domains_json=__import__("json").dumps(allowed_domains),
            is_active=True,
        )
        database.add(source)
        database.flush()
        return source.id


def _build_meeting_catalog(
    sessions: sessionmaker[Session],
    xinhua_id: int,
) -> tuple[int, Callable[[str, str], int]]:
    """Create a meeting category, a rule, and a task-item factory.

    Returns ``(meeting_id, add_item)`` where ``add_item(candidate_url, url_for_rule)``
    returns a fresh ``CrawlTaskItem.id`` linked to the rule.
    """

    with sessions.begin() as database:
        meeting = PolicyCategory(
            code="finance_council",
            name="中央财经委员会会议",
            is_active=True,
        )
        database.add(meeting)
        database.flush()
        rule = CollectionRule(
            source_id=xinhua_id,
            category_id=meeting.id,
            name="中央财经委员会会议",
            include_keywords_json='["中央财经委员会"]',
            exclude_keywords_json="[]",
            history_years=9,
            discovery_config_json='{"rss_urls":["https://www.news.cn/rss/politics.xml"]}',
            is_active=True,
        )
        database.add(rule)
        database.flush()
        meeting_id = meeting.id
        rule_id = rule.id

    def add_item(candidate_url: str, _url: str) -> int:
        with sessions.begin() as database:
            task = CrawlTask(
                rule_id=rule_id,
                trigger_type="manual",
                status="running",
                request_snapshot_json="{}",
            )
            database.add(task)
            database.flush()
            item = CrawlTaskItem(
                task_id=task.id,
                candidate_url=candidate_url,
                status="stored",
            )
            database.add(item)
            database.flush()
            return item.id

    return meeting_id, add_item


def test_cross_source_lower_priority_hits_existing_meeting_returns_duplicate(
    migrated_database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    """A people.cn article about a meeting already in news.cn is a duplicate."""

    _engine, sessions, _database_path = migrated_database
    xinhua_id = _add_source(sessions, "xinhua", "新华网", "新华社", "https://www.news.cn/", ["news.cn"])
    people_id = _add_source(
        sessions,
        "people_daily",
        "人民日报",
        "人民日报社",
        "https://politics.people.com.cn/",
        ["people.com.cn"],
    )
    meeting_id, add_item = _build_meeting_catalog(sessions, xinhua_id)

    item_xinhua = add_item(
        "https://www.news.cn/politics/20210817/c_1.html",
        "https://www.news.cn/politics/leaders/2021-08/17/c_1.htm",
    )
    item_people = add_item(
        "https://politics.people.com.cn/n1/2021/0817/c2-3.html",
        "https://politics.people.com.cn/n1/2021/0817/c2-3.html",
    )

    title = "中央财经委员会第十次会议"
    published = datetime(2021, 8, 17, 10, tzinfo=SHANGHAI)
    xinhua_record = article_record(
        xinhua_id,
        meeting_id,
        title=title,
        canonical_url="https://www.news.cn/politics/leaders/2021-08/17/c_1.htm",
        published_at=published,
        content_text="新华社通稿正文。",
    )
    people_record = article_record(
        people_id,
        meeting_id,
        title=title,
        canonical_url="https://politics.people.com.cn/n1/2021/0817/c2-3.html",
        publisher="人民日报",
        published_at=published,
        content_text="人民日报转载正文。",
    )

    service = PolicyService(sessions)
    first = service.upsert(xinhua_record, task_item_id=item_xinhua)
    second = service.upsert(people_record, task_item_id=item_people)

    assert first.outcome == "stored"
    assert second.outcome == "duplicate"
    assert first.policy_id == second.policy_id

    with sessions() as database:
        rows = list(database.scalars(select(Policy).where(Policy.category_id == meeting_id)))
        assert len(rows) == 1
        assert rows[0].source_id == xinhua_id
        revisions = list(
            database.scalars(select(PolicyRevision).where(PolicyRevision.policy_id == rows[0].id))
        )
        assert revisions == []


def test_cross_source_higher_priority_upgrades_existing_meeting(
    migrated_database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    """An xinhua article about a meeting already in people.cn replaces it."""

    _engine, sessions, _database_path = migrated_database
    xinhua_id = _add_source(sessions, "xinhua", "新华网", "新华社", "https://www.news.cn/", ["news.cn"])
    people_id = _add_source(
        sessions,
        "people_daily",
        "人民日报",
        "人民日报社",
        "https://politics.people.com.cn/",
        ["people.com.cn"],
    )
    meeting_id, add_item = _build_meeting_catalog(sessions, xinhua_id)

    item_people = add_item(
        "https://politics.people.com.cn/n1/2021/0817/c4-5.html",
        "https://politics.people.com.cn/n1/2021/0817/c4-5.html",
    )
    item_xinhua = add_item(
        "https://www.news.cn/politics/20210817/c_6.html",
        "https://www.news.cn/politics/leaders/2021-08/17/c_6.htm",
    )

    title = "中央财经委员会第十次会议"
    published = datetime(2021, 8, 17, 10, tzinfo=SHANGHAI)
    people_record = article_record(
        people_id,
        meeting_id,
        title=title,
        canonical_url="https://politics.people.com.cn/n1/2021/0817/c4-5.html",
        publisher="人民日报",
        published_at=published,
        content_text="人民日报转载正文。",
    )
    xinhua_record = article_record(
        xinhua_id,
        meeting_id,
        title=title,
        canonical_url="https://www.news.cn/politics/leaders/2021-08/17/c_6.htm",
        published_at=published,
        content_text="新华社通稿正文。",
    )

    service = PolicyService(sessions)
    first = service.upsert(people_record, task_item_id=item_people)
    second = service.upsert(xinhua_record, task_item_id=item_xinhua)

    assert first.outcome == "stored"
    assert second.outcome == "updated"
    assert first.policy_id == second.policy_id

    with sessions() as database:
        rows = list(database.scalars(select(Policy).where(Policy.category_id == meeting_id)))
        assert len(rows) == 1
        upgraded = rows[0]
        assert upgraded.source_id == xinhua_id
        assert upgraded.canonical_url.endswith("/c_6.htm")
        assert upgraded.content_text == "新华社通稿正文。"
        revisions = list(
            database.scalars(select(PolicyRevision).where(PolicyRevision.policy_id == upgraded.id))
        )
        assert len(revisions) == 1
        assert revisions[0].content_text == "人民日报转载正文。"


def test_same_source_meeting_key_falls_through_to_source_scoped_dedup(
    migrated_database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    """Same source + same meeting key must not short-circuit as duplicate.

    A different article (different URL, different content) from the same
    source about the same meeting is a *separate* Xinhua dispatch — both
    are valid independent records, so the cross-source meeting-key check
    must not collapse them. The source-scoped dedup only fires on
    ``(source_id, canonical_url)`` or ``(source_id, content_hash)`` matches.
    """

    _engine, sessions, _database_path = migrated_database
    xinhua_id = _add_source(sessions, "xinhua", "新华网", "新华社", "https://www.news.cn/", ["news.cn"])
    meeting_id, add_item = _build_meeting_catalog(sessions, xinhua_id)

    item1 = add_item(
        "https://www.news.cn/politics/20210817/a.html",
        "https://www.news.cn/politics/leaders/2021-08/17/a.htm",
    )
    item2 = add_item(
        "https://www.news.cn/politics/20210817/b.html",
        "https://www.news.cn/politics/leaders/2021-08/17/b.htm",
    )

    title = "中央财经委员会第十次会议"
    published = datetime(2021, 8, 17, 10, tzinfo=SHANGHAI)
    first_record = article_record(
        xinhua_id,
        meeting_id,
        title=title,
        canonical_url="https://www.news.cn/politics/leaders/2021-08/17/a.htm",
        published_at=published,
        content_text="第一版正文。",
    )
    second_record = article_record(
        xinhua_id,
        meeting_id,
        title=title,
        canonical_url="https://www.news.cn/politics/leaders/2021-08/17/b.htm",
        published_at=published,
        content_text="第二版正文。",
    )

    service = PolicyService(sessions)
    first = service.upsert(first_record, task_item_id=item1)
    second = service.upsert(second_record, task_item_id=item2)

    assert first.outcome == "stored"
    assert second.outcome == "stored"
    assert first.policy_id != second.policy_id

    with sessions() as database:
        rows = list(database.scalars(select(Policy).where(Policy.category_id == meeting_id)))
        assert len(rows) == 2


def test_source_priority_orders_xinhua_above_people_cn_above_cctv() -> None:
    from policy_analysis.policies.service import _source_priority

    class _Stub:
        def __init__(self, domains: list[str]) -> None:
            self.allowed_domains_json = __import__("json").dumps(domains)

    assert _source_priority(_Stub(["news.cn"])) == 3
    assert _source_priority(_Stub(["xinhuanet.com"])) == 3
    assert _source_priority(_Stub(["people.com.cn"])) == 2
    assert _source_priority(_Stub(["cctv.com"])) == 1
    assert _source_priority(_Stub([])) == 0
