from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest
from policy_analysis.core.errors import APIError
from policy_analysis.sources.models import (
    CollectionRule,
    PolicyCategory,
    Schedule,
    SeedUrl,
    Source,
)
from policy_analysis.sources.schemas import (
    CollectionRuleCreate,
    CollectionRuleUpdate,
    ScheduleCreate,
    ScheduleUpdate,
    SeedUrlImport,
)
from policy_analysis.sources.service import SourceService
from policy_analysis.sources.url_validation import normalized_http_hostname
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


@pytest.fixture
def source_catalog(database_sessions: sessionmaker[Session]) -> None:
    with database_sessions.begin() as database:
        database.add_all(
            [
                PolicyCategory(
                    code="politburo_meeting",
                    name="中央政治局会议",
                    description="新华社中央政治局会议通报",
                    is_active=True,
                ),
                PolicyCategory(
                    code="inactive_category",
                    name="停用类别",
                    description=None,
                    is_active=False,
                ),
                Source(
                    code="xinhua",
                    name="新华网",
                    organization="新华社",
                    base_url="https://www.news.cn/",
                    adapter_type="xinhua",
                    allowed_domains_json=json.dumps(["news.cn", "xn--fiqs8s.example"], ensure_ascii=False),
                    is_active=True,
                ),
                Source(
                    code="inactive_source",
                    name="停用来源",
                    organization="测试机构",
                    base_url="https://inactive.example/",
                    adapter_type="test",
                    allowed_domains_json='["inactive.example"]',
                    is_active=False,
                ),
            ]
        )


@pytest.fixture
def source_service(database_sessions: sessionmaker[Session], source_catalog: None) -> SourceService:
    del source_catalog
    return SourceService(database_sessions, now=lambda: NOW)


def valid_rule_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "  中央政治局会议  ",
        "source_code": "xinhua",
        "category_code": "politburo_meeting",
        "include_keywords": [" 中共中央政治局召开会议 ", "中共中央政治局召开会议"],
        "exclude_keywords": [" 视频 ", "视频"],
        "history_years": 5,
        "discovery": {
            "rss_urls": ["https://WWW.NEWS.CN./politics/news_politics.xml"],
            "channel_urls": ["https://sub.news.cn/politics/"],
        },
    }
    payload.update(overrides)
    return payload


def create_rule(service: SourceService, **overrides: object):
    return service.create_rule(CollectionRuleCreate.model_validate(valid_rule_payload(**overrides)))


def assert_api_error(code: str, status_code: int, operation) -> APIError:
    with pytest.raises(APIError) as caught:
        operation()
    assert caught.value.code == code
    assert caught.value.status_code == status_code
    return caught.value


def test_rule_creation_normalizes_unicode_collections_and_returns_detached_dto(
    source_service: SourceService,
) -> None:
    rule = create_rule(source_service)

    assert rule.name == "中央政治局会议"
    assert rule.history_years == 5
    assert rule.include_keywords == ["中共中央政治局召开会议"]
    assert rule.exclude_keywords == ["视频"]
    assert rule.discovery.rss_urls == ["https://WWW.NEWS.CN./politics/news_politics.xml"]
    assert rule.source.code == "xinhua"
    assert rule.category.code == "politburo_meeting"
    assert rule.created_at.tzinfo is not None
    assert rule.updated_at.tzinfo is not None
    assert source_service.list_rules()[0] == rule


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"history_years": 0},
        {"history_years": 21},
        {"include_keywords": []},
        {"include_keywords": ["   "]},
        {"discovery": {"rss_urls": [], "channel_urls": []}},
    ],
)
def test_rule_schema_rejects_invalid_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CollectionRuleCreate.model_validate(valid_rule_payload(**overrides))


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"source_code": "missing"}, "SOURCE_NOT_FOUND"),
        ({"category_code": "missing"}, "CATEGORY_NOT_FOUND"),
        ({"source_code": "inactive_source"}, "SOURCE_INACTIVE"),
        ({"category_code": "inactive_category"}, "CATEGORY_INACTIVE"),
    ],
)
def test_rule_creation_rejects_unknown_or_inactive_catalog_binding(
    source_service: SourceService,
    overrides: dict[str, object],
    code: str,
) -> None:
    error = assert_api_error(
        code,
        404 if code.endswith("NOT_FOUND") else 422,
        lambda: create_rule(source_service, **overrides),
    )
    assert "sqlite" not in error.message.lower()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://news.cn/article",
        "https://user:password@news.cn/article",
        "https:///missing-host",
        "https://evilnews.cn/article",
        "https://news.cn.evil.example/article",
        "https://example/article",
    ],
)
def test_rule_rejects_unsafe_or_disallowed_discovery_urls(source_service: SourceService, url: str) -> None:
    try:
        payload = CollectionRuleCreate.model_validate(
            valid_rule_payload(discovery={"rss_urls": [url], "channel_urls": []})
        )
    except ValidationError:
        return
    assert_api_error("URL_NOT_ALLOWED", 422, lambda: source_service.create_rule(payload))


@pytest.mark.parametrize(
    "url",
    [
        "https://news.cn/article",
        "https://sub.news.cn/article",
        "https://NEWS.CN./article",
        "https://中国.example/article",
    ],
)
def test_rule_accepts_exact_subdomain_case_trailing_dot_and_idna_hosts(
    source_service: SourceService, url: str
) -> None:
    rule = create_rule(
        source_service,
        discovery={"rss_urls": [url], "channel_urls": []},
    )
    assert rule.discovery.rss_urls == [url]


@pytest.mark.parametrize(
    "url",
    [
        "https://faß.de/article",
        "https://ς.gr/article",
        "https://σ.gr/article",
        "https://BÜCHER.example/article",
    ],
)
def test_url_hostname_normalization_matches_httpx_idna2008(url: str) -> None:
    assert normalized_http_hostname(url) == httpx.URL(url).raw_host.decode("ascii")


@pytest.mark.parametrize("hostname", ["ab\u200c.cd", "ab\u200d.cd"])
def test_url_hostname_rejects_context_invalid_zero_width_joiners(hostname: str) -> None:
    with pytest.raises(httpx.InvalidURL):
        httpx.URL(f"https://{hostname}/article")
    with pytest.raises(ValueError):
        normalized_http_hostname(f"https://{hostname}/article")


def test_rule_does_not_confuse_idna2003_transitional_allowed_domain(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    with database_sessions.begin() as database:
        database.add(
            Source(
                code="idna_boundary",
                name="IDNA 边界来源",
                organization="测试机构",
                base_url="https://fass.de/",
                adapter_type="test",
                allowed_domains_json='["fass.de"]',
                is_active=True,
            )
        )

    assert_api_error(
        "URL_NOT_ALLOWED",
        422,
        lambda: create_rule(
            source_service,
            source_code="idna_boundary",
            discovery={"rss_urls": ["https://faß.de/article"], "channel_urls": []},
        ),
    )

    with database_sessions.begin() as database:
        source = database.scalar(select(Source).where(Source.code == "idna_boundary"))
        assert source is not None
        source.allowed_domains_json = '["faß.de"]'
    accepted = create_rule(
        source_service,
        source_code="idna_boundary",
        discovery={"rss_urls": ["https://faß.de/article"], "channel_urls": []},
    )
    assert accepted.source.allowed_domains == ["xn--fa-hia.de"]


def test_schema_and_service_reject_ambiguous_or_non_dns_url_hosts(
    source_service: SourceService,
) -> None:
    rule = create_rule(source_service)
    unsafe_urls = [
        r"https://169.254.169.254\.news.cn/latest/meta-data",
        "https://news.cn/with\x00nul",
        "https://news.cn/with\x1fcontrol",
        "https://news.cn/with\x7fcontrol",
        "https://news.cn/with\x80control",
        "https://169.254.169.254%5C.news.cn/latest/meta-data",
        "https://bad..news.cn/article",
        "https://_service.news.cn/article",
        "https://-bad.news.cn/article",
        "https://bad-.news.cn/article",
        f"https://{'a' * 64}.news.cn/article",
    ]

    for url in unsafe_urls:
        with pytest.raises(ValidationError):
            SeedUrlImport.model_validate(
                {
                    "url": url,
                    "expected_title": "非法 URL 不应进入采集",
                    "expected_published_date": date(2026, 7, 30),
                }
            )
        unchecked = SeedUrlImport.model_construct(
            url=url,
            expected_title="非法 URL 不应进入采集",
            expected_published_date=date(2026, 7, 30),
            is_verified=False,
        )
        assert_api_error(
            "URL_NOT_ALLOWED",
            422,
            lambda unchecked=unchecked: source_service.import_seed_urls(rule.id, [unchecked]),
        )


def test_patch_merges_before_revalidating_every_rule_invariant(
    source_service: SourceService,
) -> None:
    original = create_rule(source_service)

    assert_api_error(
        "VALIDATION_ERROR",
        422,
        lambda: source_service.update_rule(original.id, CollectionRuleUpdate.model_validate({})),
    )
    with pytest.raises(ValidationError):
        CollectionRuleUpdate.model_validate({"include_keywords": []})
    with pytest.raises(ValidationError):
        CollectionRuleUpdate.model_validate({"discovery": {"rss_urls": [], "channel_urls": []}})
    assert_api_error(
        "SOURCE_NOT_FOUND",
        404,
        lambda: source_service.update_rule(
            original.id,
            CollectionRuleUpdate.model_validate({"source_code": "missing"}),
        ),
    )

    updated = source_service.update_rule(
        original.id,
        CollectionRuleUpdate.model_validate({"name": " 更新后的会议规则 ", "exclude_keywords": ["直播"]}),
    )
    assert updated.name == "更新后的会议规则"
    assert updated.include_keywords == original.include_keywords
    assert updated.exclude_keywords == ["直播"]
    assert updated.discovery == original.discovery


def test_patch_revalidates_stored_rule_and_persists_schema_normalization(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    invalid_mutations = [
        ("name", "   "),
        ("include_keywords_json", json.dumps(["关键词"] * 65, ensure_ascii=False)),
        ("include_keywords_json", json.dumps(["过" * 129], ensure_ascii=False)),
    ]
    for field_name, bad_value in invalid_mutations:
        rule = create_rule(source_service)
        with database_sessions.begin() as database:
            stored = database.get(CollectionRule, rule.id)
            assert stored is not None
            setattr(stored, field_name, bad_value)

        assert_api_error(
            "RULE_CONFIGURATION_INVALID",
            422,
            lambda rule_id=rule.id: source_service.update_rule(
                rule_id,
                CollectionRuleUpdate.model_validate({"is_active": False}),
            ),
        )
        with database_sessions() as database:
            stored = database.get(CollectionRule, rule.id)
            assert stored is not None
            assert getattr(stored, field_name) == bad_value
            assert stored.is_active is True

    rule = create_rule(source_service)
    with database_sessions.begin() as database:
        stored = database.get(CollectionRule, rule.id)
        assert stored is not None
        stored.name = " 现场规则 "
        stored.include_keywords_json = json.dumps([" 政策 ", "政策"], ensure_ascii=False)
        stored.exclude_keywords_json = json.dumps([" 视频 ", "视频"], ensure_ascii=False)

    updated = source_service.update_rule(
        rule.id,
        CollectionRuleUpdate.model_validate({"is_active": False}),
    )
    assert updated.name == "现场规则"
    assert updated.include_keywords == ["政策"]
    assert updated.exclude_keywords == ["视频"]
    assert updated.source.code == rule.source.code
    assert updated.category.code == rule.category.code
    assert updated.history_years == rule.history_years
    assert updated.discovery == rule.discovery
    with database_sessions() as database:
        stored = database.get(CollectionRule, rule.id)
        assert stored is not None
        assert stored.name == "现场规则"
        assert stored.include_keywords_json == '["政策"]'
        assert stored.exclude_keywords_json == '["视频"]'

    repairable = create_rule(source_service)
    with database_sessions.begin() as database:
        stored = database.get(CollectionRule, repairable.id)
        assert stored is not None
        stored.include_keywords_json = "not-json"
    repaired = source_service.update_rule(
        repairable.id,
        CollectionRuleUpdate.model_validate({"include_keywords": [" 修复后的关键词 "], "is_active": False}),
    )
    assert repaired.include_keywords == ["修复后的关键词"]
    assert repaired.is_active is False

    still_invalid = create_rule(source_service)
    with database_sessions.begin() as database:
        stored = database.get(CollectionRule, still_invalid.id)
        assert stored is not None
        stored.name = "   "
        stored.include_keywords_json = "not-json"
    assert_api_error(
        "RULE_CONFIGURATION_INVALID",
        422,
        lambda: source_service.update_rule(
            still_invalid.id,
            CollectionRuleUpdate.model_validate({"include_keywords": ["只修复一个字段"], "is_active": False}),
        ),
    )
    with database_sessions() as database:
        stored = database.get(CollectionRule, still_invalid.id)
        assert stored is not None
        assert stored.name == "   "
        assert stored.include_keywords_json == "not-json"
        assert stored.is_active is True


def test_disabled_rule_can_keep_inactive_binding_but_cannot_be_reenabled(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    rule = create_rule(source_service, is_active=False)
    with database_sessions.begin() as database:
        source = database.scalar(select(Source).where(Source.code == "xinhua"))
        assert source is not None
        source.is_active = False

    renamed = source_service.update_rule(rule.id, CollectionRuleUpdate.model_validate({"name": "停用保留"}))
    assert renamed.is_active is False
    assert_api_error(
        "SOURCE_INACTIVE",
        422,
        lambda: source_service.update_rule(rule.id, CollectionRuleUpdate.model_validate({"is_active": True})),
    )


@pytest.mark.parametrize(
    "bad_json",
    ["not-json-secret", "{}", '["news.cn", 7]', "[]"],
)
def test_bad_allowed_domain_storage_produces_controlled_error_without_raw_value(
    database_sessions: sessionmaker[Session],
    source_catalog: None,
    bad_json: str,
) -> None:
    del source_catalog
    with database_sessions.begin() as database:
        source = database.scalar(select(Source).where(Source.code == "xinhua"))
        assert source is not None
        source.allowed_domains_json = bad_json
    service = SourceService(database_sessions)

    error = assert_api_error("SOURCE_CONFIGURATION_INVALID", 422, service.list_sources)
    assert bad_json not in error.message


@pytest.mark.parametrize("cron", ["0 2 * *", "0 2 * * * extra", "61 2 * * *", "bad cron value x y"])
def test_schedule_rejects_non_five_field_or_invalid_cron(source_service: SourceService, cron: str) -> None:
    rule = create_rule(source_service)
    assert_api_error(
        "INVALID_CRON",
        422,
        lambda: source_service.create_schedule(
            ScheduleCreate.model_validate({"rule_id": rule.id, "cron_expression": cron})
        ),
    )


def test_schedule_lifecycle_uses_shanghai_cron_and_preserves_last_run(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    rule = create_rule(source_service)
    schedule = source_service.create_schedule(
        ScheduleCreate.model_validate({"rule_id": rule.id, "cron_expression": " 0  9 * * * "})
    )
    assert schedule.cron_expression == "0 9 * * *"
    assert schedule.timezone == "Asia/Shanghai"
    assert schedule.is_active is False
    assert schedule.next_run_at is None

    enabled = source_service.update_schedule(schedule.id, ScheduleUpdate.model_validate({"is_active": True}))
    assert enabled.next_run_at == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    with database_sessions.begin() as database:
        stored = database.get(Schedule, schedule.id)
        assert stored is not None
        stored.last_run_at = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)

    changed = source_service.update_schedule(
        schedule.id,
        ScheduleUpdate.model_validate({"cron_expression": "30 10 * * *"}),
    )
    assert changed.next_run_at == datetime(2026, 8, 1, 2, 30, tzinfo=UTC)
    assert changed.last_run_at == datetime(2026, 7, 31, 1, 0, tzinfo=UTC)

    disabled = source_service.update_schedule(
        schedule.id, ScheduleUpdate.model_validate({"is_active": False})
    )
    assert disabled.next_run_at is None
    assert disabled.last_run_at == changed.last_run_at
    assert_api_error(
        "VALIDATION_ERROR",
        422,
        lambda: source_service.update_schedule(schedule.id, ScheduleUpdate.model_validate({})),
    )


def test_schedule_rejects_missing_rule_and_missing_resources(source_service: SourceService) -> None:
    assert_api_error(
        "RULE_NOT_FOUND",
        404,
        lambda: source_service.create_schedule(
            ScheduleCreate.model_validate({"rule_id": 9999, "cron_expression": "0 9 * * *"})
        ),
    )
    assert_api_error(
        "SCHEDULE_NOT_FOUND",
        404,
        lambda: source_service.update_schedule(9999, ScheduleUpdate.model_validate({"is_active": True})),
    )


def seed(url: str, title: str = "中共中央政治局召开会议") -> SeedUrlImport:
    return SeedUrlImport.model_validate(
        {
            "url": url,
            "expected_title": title,
            "expected_published_date": date(2026, 7, 30),
            "is_verified": True,
        }
    )


def test_seed_import_is_idempotent_preserves_site_data_and_only_adds_new_entries(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    rule = create_rule(source_service)
    first_url = "https://news.cn/20260730/one/c.html"
    extra_url = "https://news.cn/site-added/c.html"
    result = source_service.import_seed_urls(rule.id, [seed(first_url), seed(first_url)])
    assert (result.inserted, result.existing) == (1, 0)

    with database_sessions.begin() as database:
        existing = database.scalar(select(SeedUrl).where(SeedUrl.url == first_url))
        assert existing is not None
        existing.expected_title = "现场人工标题"
        existing.expected_published_date = date(2026, 7, 29)
        existing.is_verified = False
        database.add(
            SeedUrl(
                rule_id=rule.id,
                url=extra_url,
                expected_title="现场额外种子",
                expected_published_date=date(2026, 7, 28),
                is_verified=True,
            )
        )

    second_url = "https://sub.news.cn/20260731/two/c.html"
    result = source_service.import_seed_urls(
        rule.id,
        [seed(first_url, "资源新版标题"), seed(second_url)],
    )
    assert (result.inserted, result.existing) == (1, 1)
    with database_sessions() as database:
        rows = list(database.scalars(select(SeedUrl).where(SeedUrl.rule_id == rule.id)))
        assert {row.url for row in rows} == {first_url, second_url, extra_url}
        existing = next(row for row in rows if row.url == first_url)
        assert existing.expected_title == "现场人工标题"
        assert existing.expected_published_date == date(2026, 7, 29)
        assert existing.is_verified is False


def test_invalid_seed_rolls_back_entire_batch_and_failure_does_not_poison_next_call(
    source_service: SourceService,
    database_sessions: sessionmaker[Session],
) -> None:
    rule = create_rule(source_service)
    good = seed("https://news.cn/good/c.html")
    bad = seed("https://news.cn.evil.example/bad/c.html")

    assert_api_error(
        "URL_NOT_ALLOWED",
        422,
        lambda: source_service.import_seed_urls(rule.id, [good, bad]),
    )
    with database_sessions() as database:
        assert database.scalar(select(SeedUrl).where(SeedUrl.rule_id == rule.id)) is None

    assert source_service.import_seed_urls(rule.id, [good]).inserted == 1


def test_catalog_and_rule_lists_are_stably_sorted(source_service: SourceService) -> None:
    assert [item.code for item in source_service.list_categories()] == [
        "inactive_category",
        "politburo_meeting",
    ]
    assert [item.code for item in source_service.list_sources()] == ["inactive_source", "xinhua"]
    first = create_rule(source_service, name="第一条")
    second = create_rule(source_service, name="第二条")
    assert [item.id for item in source_service.list_rules()] == [first.id, second.id]
