from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from policy_analysis.collectors.base import DiscoveredLink, ExtractedArticle
from policy_analysis.collectors.xinhua import XinhuaCollector

FIXTURES = Path(__file__).with_name("fixtures")
INCLUDE = ("中共中央政治局召开会议",)
BEIJING_OFFSET_SECONDS = 8 * 60 * 60
CURRENT_ARTICLE_URL = "https://www.news.cn/politics/leaders/20260227/a8b27b1b8c7442be9678ff6e530cdd18/c.html"
VIDEO_ARTICLE_URL = "https://www.news.cn/20260130/e9daba7d39a040b2b52eb85cc1bf894a/c.html"


def _load_fixture(name: str) -> ExtractedArticle:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert {"request_id", "adapter", "adapter_version", "artifact_id", "data"} <= payload.keys()
    assert payload["adapter"] == "generic.article"
    data = payload["data"]
    assert isinstance(data, dict)
    assert {"title", "content", "author", "date"} <= data.keys()
    return ExtractedArticle(
        request_id=payload["request_id"],
        artifact_id=payload["artifact_id"],
        title=data["title"],
        content=data["content"],
        author=data["author"],
        published_hint=data["date"],
    )


def _collector(*, minimum_content_chars: int = 80) -> XinhuaCollector:
    return XinhuaCollector(
        allowed_domains={"news.cn", "www.news.cn", "xinhuanet.com", "www.xinhuanet.com"},
        include_keywords=INCLUDE,
        exclude_keywords=("视频",),
        minimum_content_chars=minimum_content_chars,
    )


def _article(**changes: str) -> ExtractedArticle:
    content = (
        "新华社北京7月30日电 中共中央政治局召开会议。"
        "会议分析研究当前形势，并审议有关文件。"
        "会议还研究了其他事项，并对下一阶段工作作出明确部署。"
        "这是为适配器判定保留的简短测试文本，不包含公开报道全文。"
    )
    values = {
        "title": "中共中央政治局召开会议 中共中央总书记习近平主持会议",
        "content": content,
        "author": "新华网",
        "published_hint": "2026-07-30T14:00:00+08:00",
    }
    values.update(changes)
    return replace(_load_fixture("xinhua_current_article.json"), **values)


def test_contract_fixtures_map_to_real_extracted_articles_and_ignore_unknown_fields() -> None:
    old = _load_fixture("xinhua_old_article.json")
    current = _load_fixture("xinhua_current_article.json")
    video = _load_fixture("xinhua_video_article.json")

    assert (old.artifact_id, current.artifact_id, video.artifact_id) == (
        "test-xinhua-old-artifact",
        "test-xinhua-current-artifact",
        "test-xinhua-video-artifact",
    )
    assert all(isinstance(article, ExtractedArticle) for article in (old, current, video))


def test_accepts_old_and_current_official_articles_and_rejects_video_only_article() -> None:
    collector = _collector()
    cutoff = datetime(2021, 7, 31, tzinfo=UTC)

    old = collector.classify(
        "https://WWW.NEWS.CN:443/2021-10/18/c_1127969449.htm?utm_source=test#top",
        _load_fixture("xinhua_old_article.json"),
        cutoff,
    )
    current = collector.classify(
        CURRENT_ARTICLE_URL,
        _load_fixture("xinhua_current_article.json"),
        cutoff,
    )
    video = collector.classify(
        VIDEO_ARTICLE_URL,
        _load_fixture("xinhua_video_article.json"),
        cutoff,
    )

    assert old.accepted is True
    assert old.reason_code == "ACCEPTED"
    assert old.canonical_url == "https://www.news.cn/2021-10/18/c_1127969449.htm"
    assert old.published_at == datetime(2021, 10, 18, 15, 3, 24, tzinfo=old.published_at.tzinfo)
    assert current.accepted is True
    assert current.canonical_url == CURRENT_ARTICLE_URL
    assert current.published_at == datetime(2026, 2, 27, 13, 27, 37, tzinfo=current.published_at.tzinfo)
    assert video.accepted is False
    assert video.reason_code == "VIDEO_ONLY"


def test_canonicalize_removes_only_tracking_data_and_is_idempotent() -> None:
    collector = _collector()
    original = (
        "HTTPS://WWW.News.CN:443/path/to/c.html?utm_source=x&A=1&a=2&FROM=feed&empty=&spm=s"
        "&article_id=7#section"
    )

    canonical = collector.canonicalize(original)

    assert canonical == "https://www.news.cn/path/to/c.html?A=1&a=2&empty=&article_id=7"
    assert collector.canonicalize(canonical) == canonical
    assert collector.canonicalize("http://news.cn:80") == "http://news.cn/"
    assert collector.canonicalize("https://news.cn:8443/a") == "https://news.cn:8443/a"
    assert collector.canonicalize("http://www.xinhuanet.com/a") == "http://www.xinhuanet.com/a"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.news.cn/private?token=secret",
        "https://user:password@www.news.cn/private",
        "https://www.news.cn\\@evil.test/private",
        "https://www.news.cn/a\x00b",
        "https://www.news.cn:bad/private",
        "https://\ud800.news.cn/private",
        "https:///missing-host",
    ],
)
def test_canonicalize_rejects_unsafe_urls_with_one_sanitized_error(url: str) -> None:
    with pytest.raises(ValueError) as caught:
        _collector().canonicalize(url)

    assert str(caught.value) == "URL_INVALID"
    assert "secret" not in str(caught.value)
    assert "password" not in str(caught.value)


def test_rss_discovery_resolves_filters_deduplicates_and_preserves_first_order() -> None:
    xml = """<?xml version="1.0"?>
    <rss><channel>
      <item><title> 中共中央政治局召开会议 </title>
        <link>https://WWW.NEWS.CN:443/2021-10/18/a.htm?utm_source=rss</link></item>
      <item><title>中共中央政治局召开会议 重复项</title>
        <link>https://www.news.cn/2021-10/18/a.htm#again</link></item>
      <item><title>中共中央政治局召开会议 当前稿</title>
        <link>../20260227/id/c.html?article_id=2&amp;spm=feed</link></item>
      <item><title>中共中央政治局召开会议 视频</title>
        <link>https://www.news.cn/video/c.html</link></item>
      <item><title>中共中央政治局召开会议 跨域</title>
        <link>https://evil.test/a</link></item>
      <item><title>无关标题</title><link>https://www.news.cn/unrelated</link></item>
      <item><title></title><link>https://www.news.cn/empty-title</link></item>
      <item><title>中共中央政治局召开会议</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""
    origin = "https://www.news.cn/politics/index.html"

    assert _collector().discover_from_rss(xml, origin) == [
        DiscoveredLink(
            "https://www.news.cn/2021-10/18/a.htm",
            "中共中央政治局召开会议",
            origin,
        ),
        DiscoveredLink(
            "https://www.news.cn/20260227/id/c.html?article_id=2",
            "中共中央政治局召开会议 当前稿",
            origin,
        ),
    ]


@pytest.mark.parametrize(
    "xml",
    [
        "<rss><channel><item></rss>",
        '<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel>&xxe;</channel></rss>',
    ],
)
def test_rss_discovery_rejects_malformed_or_entity_xml_with_stable_error(xml: str) -> None:
    with pytest.raises(ValueError) as caught:
        _collector().discover_from_rss(xml, "https://www.news.cn/index.html")

    assert str(caught.value) == "RSS_INVALID"
    assert xml not in str(caught.value)


def test_link_discovery_handles_relative_absolute_and_dirty_items_without_leaking_errors() -> None:
    origin = "https://www.news.cn/politics/index.html"
    links: list[Any] = [
        {
            "href": "/20260227/id/c.html?utm_medium=column",
            "text": " 中共中央政治局召开会议   重要稿 ",
        },
        {"href": "https://www.news.cn/20260227/id/c.html#dup", "text": "中共中央政治局召开会议"},
        {"href": "//www.news.cn/20260301/id/c.html#top", "text": "中共中央政治局召开会议 同域稿"},
        {"href": "//evil.test/c.html", "text": "中共中央政治局召开会议"},
        {"href": "#section", "text": "中共中央政治局召开会议 栏目锚点"},
        {"href": "?page=2", "text": "中共中央政治局召开会议 栏目翻页"},
        {"href": "javascript:alert(1)", "text": "中共中央政治局召开会议"},
        {"href": "https://www.news.cn/video", "text": "中共中央政治局召开会议 视频"},
        {"href": "https://evil.test/a", "text": "中共中央政治局召开会议"},
        {"href": "https://www.news.cn:bad/a", "text": "中共中央政治局召开会议 坏端口"},
        {"href": None, "text": "中共中央政治局召开会议"},
        {"href": "https://www.news.cn/no-text", "text": None},
        {"text": "中共中央政治局召开会议"},
        None,
    ]

    assert _collector().discover_from_links(links, origin) == [
        DiscoveredLink(
            "https://www.news.cn/20260227/id/c.html",
            "中共中央政治局召开会议 重要稿",
            origin,
        ),
        DiscoveredLink(
            "https://www.news.cn/20260301/id/c.html",
            "中共中央政治局召开会议 同域稿",
            origin,
        ),
    ]
    assert (
        _collector().discover_from_links(
            [{"href": "/relative", "text": "中共中央政治局召开会议"}],
            "not-a-valid-origin",
        )
        == []
    )


@pytest.mark.parametrize(
    ("expected_reason", "url", "article", "cutoff"),
    [
        (
            "DOMAIN_NOT_ALLOWED",
            "https://example.test/20260730/a.html",
            _article(),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "TITLE_NOT_MATCHED",
            "https://www.news.cn/20260730/a.html",
            _article(title="其他会议 视频"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "EXCLUDED_KEYWORD",
            "https://www.news.cn/20260730/a.html",
            _article(title="中共中央政治局召开会议 视频"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "VIDEO_ONLY",
            "https://www.news.cn/20260730/a.html",
            _article(content="编导：测试人员 新华社音视频部制作"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "LEAD_NOT_MATCHED",
            "https://www.news.cn/20260730/a.html",
            _article(content=("无关内容。" * 220) + "中共中央政治局召开会议。来源：新华网"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "SOURCE_NOT_OFFICIAL",
            "https://www.news.cn/20260730/a.html",
            _article(
                content="中共中央政治局召开会议。" + ("会议研究有关工作。" * 20),
                author="",
            ),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "PUBLISHED_AT_MISSING",
            "https://www.news.cn/politics/a.html",
            _article(published_hint="", content="新华社北京电 中共中央政治局召开会议。" + ("内容。" * 30)),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "OUTSIDE_WINDOW",
            "https://www.news.cn/20200730/a.html",
            _article(published_hint="2020-07-30T14:00:00+08:00"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
        (
            "CONTENT_TOO_SHORT",
            "https://www.news.cn/20260730/a.html",
            _article(content="新华社北京电 中共中央政治局召开会议。"),
            datetime(2021, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_classification_rejection_reasons_are_stable_and_keep_context(
    expected_reason: str,
    url: str,
    article: ExtractedArticle,
    cutoff: datetime,
) -> None:
    result = _collector().classify(url, article, cutoff)

    assert result.accepted is False
    assert result.reason_code == expected_reason
    assert result.canonical_url
    assert result.title == article.title
    assert result.content == article.content
    assert result.artifact_id == article.artifact_id


@pytest.mark.parametrize(
    ("expected_reason", "url", "article", "cutoff"),
    [
        pytest.param(
            "DOMAIN_NOT_ALLOWED",
            "https://outside.test/20260730/a.html",
            _article(title="无关标题"),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="domain-before-title",
        ),
        pytest.param(
            "TITLE_NOT_MATCHED",
            "https://www.news.cn/20260730/a.html",
            _article(title="无关标题 视频"),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="title-before-excluded",
        ),
        pytest.param(
            "EXCLUDED_KEYWORD",
            "https://www.news.cn/20260730/a.html",
            _article(
                title="中共中央政治局召开会议 视频",
                content="新华社北京电 中共中央政治局召开会议。编导：测试人员 新华社音视频部制作",
            ),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="excluded-before-video",
        ),
        pytest.param(
            "VIDEO_ONLY",
            "https://www.news.cn/20260730/a.html",
            _article(content="编导：测试人员 新华社音视频部制作"),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="video-before-lead",
        ),
        pytest.param(
            "LEAD_NOT_MATCHED",
            "https://www.news.cn/20260730/a.html",
            _article(content="无关内容。" * 30, author=""),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="lead-before-source",
        ),
        pytest.param(
            "SOURCE_NOT_OFFICIAL",
            "https://www.news.cn/politics/a.html",
            _article(
                content="中共中央政治局召开会议。" + ("会议内容。" * 20),
                author="",
                published_hint="",
            ),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="source-before-published-missing",
        ),
        pytest.param(
            "PUBLISHED_AT_MISSING",
            "https://www.news.cn/politics/a.html",
            _article(
                content="中共中央政治局召开会议。",
                author="新华网",
                published_hint="",
            ),
            datetime(2020, 1, 1, tzinfo=UTC),
            id="published-missing-before-content-short",
        ),
        pytest.param(
            "OUTSIDE_WINDOW",
            "https://www.news.cn/20200730/a.html",
            _article(
                content="中共中央政治局召开会议。",
                author="新华网",
                published_hint="2020-07-30T14:00:00+08:00",
            ),
            datetime(2021, 1, 1, tzinfo=UTC),
            id="outside-window-before-content-short",
        ),
    ],
)
def test_classification_uses_documented_reason_priority_when_adjacent_conditions_both_fail(
    expected_reason: str,
    url: str,
    article: ExtractedArticle,
    cutoff: datetime,
) -> None:
    assert _collector().classify(url, article, cutoff).reason_code == expected_reason


@pytest.mark.parametrize(
    ("published_hint", "content", "url", "expected"),
    [
        (
            "2026-07-30T01:02:03Z",
            "2025-01-02 03:04:05 新华社北京电 中共中央政治局召开会议。" + ("内容。" * 30),
            "https://www.news.cn/20240101/a.html",
            datetime(2026, 7, 30, 9, 2, 3),
        ),
        (
            "not-a-date",
            "2025年1月2日 新华社北京电 中共中央政治局召开会议。" + ("内容。" * 30),
            "https://www.news.cn/20240101/a.html",
            datetime(2025, 1, 2),
        ),
        (
            "not-a-date",
            "发布时间：2025-01-02T23:04:05-05:00 新华社北京电 中共中央政治局召开会议。" + ("内容。" * 30),
            "https://www.news.cn/20240101/a.html",
            datetime(2025, 1, 3, 12, 4, 5),
        ),
        (
            "2026-02-30 12:00:00",
            "2025-02-30 新华社北京电 中共中央政治局召开会议。" + ("内容。" * 30),
            "https://www.news.cn/20240131/a.html",
            datetime(2024, 1, 31),
        ),
        (
            "",
            "新华社北京7月30日电 中共中央政治局召开会议。" + ("内容。" * 30),
            "https://www.news.cn/2021-10/18/a.html",
            datetime(2021, 10, 18),
        ),
    ],
)
def test_published_time_uses_hint_then_full_body_date_then_url(
    published_hint: str,
    content: str,
    url: str,
    expected: datetime,
) -> None:
    result = _collector().classify(
        url,
        _article(published_hint=published_hint, content=content),
        datetime(2020, 1, 1),
    )

    assert result.accepted is True
    assert result.published_at is not None
    assert result.published_at.tzinfo is not None
    assert result.published_at.utcoffset().total_seconds() == BEIJING_OFFSET_SECONDS
    assert result.published_at.replace(tzinfo=None) == expected


def test_body_date_selection_uses_earliest_valid_text_match_across_supported_formats() -> None:
    article = _article(
        published_hint="",
        content=(
            "2020年1月1日 新华社北京电 中共中央政治局召开会议。"
            "这是达到正文长度要求的会议文字通报测试内容。"
            "后文引用另一时间 2026-07-30T01:02:03Z，但不得覆盖开头日期。"
        ),
    )

    result = _collector().classify(
        "https://www.news.cn/politics/a.html",
        article,
        datetime(2021, 1, 1, tzinfo=UTC),
    )

    assert result.reason_code == "OUTSIDE_WINDOW"
    assert result.published_at == datetime(2020, 1, 1, tzinfo=result.published_at.tzinfo)


def test_cutoff_is_inclusive_and_naive_values_are_interpreted_as_beijing_time() -> None:
    article = _article(published_hint="2026-07-30 14:00:00")

    result = _collector().classify(
        "https://www.news.cn/20260730/a.html",
        article,
        datetime(2026, 7, 30, 14, 0, 0),
    )

    assert result.accepted is True
    assert result.published_at is not None
    assert result.published_at.utcoffset().total_seconds() == BEIJING_OFFSET_SECONDS


def test_author_and_source_marker_set_publisher_but_incidental_tail_lead_does_not_match() -> None:
    collector = _collector()
    by_author = collector.classify(
        "https://www.news.cn/20260730/a.html",
        _article(
            author="新华社",
            content="中共中央政治局召开会议。" + ("会议内容。" * 20),
        ),
        datetime(2020, 1, 1, tzinfo=UTC),
    )
    by_marker = collector.classify(
        "https://www.news.cn/20260730/b.html",
        _article(
            author="",
            content="来源：新华社 中共中央政治局召开会议。" + ("会议内容。" * 20),
        ),
        datetime(2020, 1, 1, tzinfo=UTC),
    )
    incidental_tail = collector.classify(
        "https://www.news.cn/20260730/c.html",
        _article(content=("其他报道。" * 220) + "新华社北京电 中共中央政治局召开会议。"),
        datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert by_author.accepted is True
    assert by_author.publisher == "新华社"
    assert by_marker.accepted is True
    assert by_marker.publisher == "新华社"
    assert incidental_tail.reason_code == "LEAD_NOT_MATCHED"


def test_long_text_mentioning_video_is_not_treated_as_video_only() -> None:
    article = _article(
        content="新华社北京电 中共中央政治局召开会议。会议播放了视频材料。" + ("会议内容。" * 30)
    )

    result = _collector().classify(
        "https://www.news.cn/20260730/a.html",
        article,
        datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"allowed_domains": set()}, "ALLOWED_DOMAINS_INVALID"),
        ({"allowed_domains": {"bad_domain.example"}}, "ALLOWED_DOMAINS_INVALID"),
        ({"include_keywords": ()}, "INCLUDE_KEYWORDS_INVALID"),
        ({"include_keywords": ("",)}, "KEYWORD_INVALID"),
        ({"exclude_keywords": ("   ",)}, "KEYWORD_INVALID"),
        ({"minimum_content_chars": True}, "MINIMUM_CONTENT_CHARS_INVALID"),
        ({"minimum_content_chars": 0}, "MINIMUM_CONTENT_CHARS_INVALID"),
        ({"minimum_content_chars": 2.5}, "MINIMUM_CONTENT_CHARS_INVALID"),
    ],
)
def test_constructor_rejects_invalid_direct_calls_with_sanitized_errors(
    changes: dict[str, object], expected: str
) -> None:
    kwargs: dict[str, object] = {
        "allowed_domains": {"www.news.cn"},
        "include_keywords": INCLUDE,
        "exclude_keywords": ("视频",),
        "minimum_content_chars": 80,
    }
    kwargs.update(changes)

    with pytest.raises(ValueError) as caught:
        XinhuaCollector(**kwargs)  # type: ignore[arg-type]

    assert str(caught.value) == expected
    assert "bad_domain" not in str(caught.value)


def test_constructor_copies_and_normalizes_mutable_configuration() -> None:
    domains = {"WWW.NEWS.CN."}
    collector = XinhuaCollector(domains, INCLUDE, (" 视频 ",), 80)
    domains.clear()

    assert collector.allowed_domains == frozenset({"www.news.cn"})
    assert collector.include_keywords == INCLUDE
    assert collector.exclude_keywords == ("视频",)
    assert (
        collector.classify(
            "https://www.news.cn/20260730/a.html",
            _article(),
            datetime(2020, 1, 1, tzinfo=UTC),
        ).accepted
        is True
    )
