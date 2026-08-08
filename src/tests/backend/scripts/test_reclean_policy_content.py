from __future__ import annotations

import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.collectors.base import WebFetchClientError
from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.policies.models import Policy
from policy_analysis.policies.schemas import PolicyQuery
from policy_analysis.policies.service import PolicyService
from policy_analysis.sources.models import PolicyCategory, Source
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "reclean_policy_content.py"
NOW = datetime(2026, 8, 1, 8, tzinfo=UTC)

DIRTY_BODY = (
    "新华网 > > 正文 2022 12/ 14 11:37:37 来源：新华社 "
    "新华社北京12月14日电 中共中央政治局召开会议，分析研究当前经济形势。"
    + "重要部署。" * 5
    + " 策划：孙承斌 监制：孙志平 新华社音视频部制作 新华通讯社出品"
    + " 【纠错】 【责任编辑:吴咏玲】"
    + "\n阅读下一篇： 37 中共中央政治局召开会议 决定召开二十届五中全会"
    + "\n010020020110000000000000011100001129207254"
)
EXPECTED_CLEAN = "新华社北京12月14日电 中共中央政治局召开会议，分析研究当前经济形势。" + "重要部署。" * 5

# Paragraph-structured body recovered from re-fetched HTML (two <p> blocks).
_PARA_1 = "新华社北京12月14日电 中共中央政治局召开会议，分析研究当前经济形势。"
_PARA_2 = "重要部署。" * 5
EXPECTED_REFETCH_BODY = f"{_PARA_1}\n{_PARA_2}"

# Raw HTML for the dirty row's canonical_url: header/footer chrome live outside
# <p>, so paragraph_body recovers only the two body paragraphs.
URL_A = "https://www.news.cn/20221214/a.html"
URL_B = "https://www.news.cn/20221215/b.html"
DETAIL_HTML_A = (
    "<html><head><title>中共中央政治局召开会议</title></head><body>"
    '<div class="page-head">新华网 > 时政 > 正文 2022 12/ 14 11:37:37 来源：新华社</div>'
    '<div id="detail">'
    f"<p>{_PARA_1}</p><p>{_PARA_2}</p>"
    "</div>"
    '<div class="page-foot">阅读下一篇：37 ... 【纠错】 【责任编辑:吴咏玲】</div>'
    "</body></html>"
)
DETAIL_HTML_B = f'<div id="detail"><p>{EXPECTED_CLEAN}</p></div>'


class _FakeFetcher:
    """Stand-in for WebFetchClient.fetch_text used by --refetch tests."""

    def __init__(self, pages: dict[str, str], error_urls: set[str] | None = None) -> None:
        self.pages = pages
        self.error_urls = error_urls or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url in self.error_urls:
            raise WebFetchClientError(code="WEBFETCH_UNAVAILABLE", message="simulated", retryable=False)
        return self.pages.get(url, "")


def _load_script():
    spec = importlib.util.spec_from_file_location("reclean_policy_content", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reclean_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, sessionmaker[Session]]:
    db_path = tmp_path / "reclean.sqlite3"
    monkeypatch.setenv("POLICY_ANALYSIS_DATABASE__PATH", str(db_path))
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    engine = build_engine(db_path)
    sessions = session_factory(engine)
    try:
        with sessions.begin() as db:
            source = Source(
                code="xinhua",
                name="新华网",
                organization="新华社",
                base_url="https://www.news.cn/",
                adapter_type="xinhua",
                allowed_domains_json='["news.cn"]',
                is_active=True,
            )
            category = PolicyCategory(code="politburo", name="政治局会议", is_active=True)
            db.add_all([source, category])
            db.flush()
            for url, body, hash_value in (
                ("https://www.news.cn/20221214/a.html", DIRTY_BODY, "stale-hash-value"),
                (
                    "https://www.news.cn/20221215/b.html",
                    EXPECTED_CLEAN,
                    hashlib.sha256(EXPECTED_CLEAN.encode("utf-8")).hexdigest(),
                ),
            ):
                db.add(
                    Policy(
                        source_id=source.id,
                        category_id=category.id,
                        title="中共中央政治局召开会议",
                        canonical_url=url,
                        publisher="新华社",
                        published_at=NOW,
                        content_text=body,
                        content_hash=hash_value,
                        webfetch_artifact_id="artifact",
                        first_crawled_at=NOW,
                        last_crawled_at=NOW,
                    )
                )
        yield db_path, sessions
    finally:
        engine.dispose()


def _rows(sessions: sessionmaker[Session]) -> list[tuple[str, str, str]]:
    with sessions() as db:
        return [
            (p.canonical_url, p.content_text, p.content_hash)
            for p in db.scalars(select(Policy).order_by(Policy.id))
        ]


def test_reclean_strips_chrome_recomputes_hash_and_leaves_clean_rows(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()

    assert script.main(["--db", str(db_path)]) == 0

    with sessions() as db:
        dirty, clean = list(db.scalars(select(Policy).order_by(Policy.id)))
        assert dirty.content_text == EXPECTED_CLEAN
        assert dirty.content_hash == hashlib.sha256(EXPECTED_CLEAN.encode("utf-8")).hexdigest()
        for chrome in (
            "新华网 >",
            "来源：",
            "策划：",
            "新华通讯社出品",
            "【纠错】",
            "责任编辑",
            "阅读下一篇",
            "010020020110",
        ):
            assert chrome not in dirty.content_text
        # Already-clean row is untouched.
        assert clean.content_text == EXPECTED_CLEAN
        assert clean.content_hash == hashlib.sha256(EXPECTED_CLEAN.encode("utf-8")).hexdigest()


def test_reclean_is_idempotent(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()

    assert script.main(["--db", str(db_path)]) == 0
    before = _rows(sessions)
    assert script.main(["--db", str(db_path)]) == 0
    assert _rows(sessions) == before


def test_reclean_dry_run_does_not_write(reclean_db, capsys: pytest.CaptureFixture[str]) -> None:
    db_path, sessions = reclean_db
    script = _load_script()

    assert script.main(["--dry-run", "--db", str(db_path)]) == 0

    capsys.readouterr()  # drain the dry-run preview output
    with sessions() as db:
        dirty = db.scalar(select(Policy).where(Policy.canonical_url.contains("/a.html")))
        assert dirty is not None
        assert dirty.content_text == DIRTY_BODY
        assert dirty.content_hash == "stale-hash-value"


def test_reclean_dry_run_reports_changed_count(reclean_db, capsys: pytest.CaptureFixture[str]) -> None:
    db_path, _sessions = reclean_db
    script = _load_script()

    assert script.main(["--dry-run", "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "将变更 1 条" in out


def test_reclean_syncs_fts_index_after_update(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    service = PolicyService(sessions)

    assert script.main(["--db", str(db_path)]) == 0
    # Stripped footer term no longer resolves through the synced full-text index.
    assert service.search(PolicyQuery(full_text="阅读下一篇")).total == 0
    assert service.search(PolicyQuery(full_text="责任编辑")).total == 0
    # A body term still resolves for both rows after re-clean.
    assert service.search(PolicyQuery(full_text="重要部署")).total == 2


def test_refetch_replaces_flat_body_with_paragraph_structured_body(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    fetcher = _FakeFetcher({URL_A: DETAIL_HTML_A, URL_B: DETAIL_HTML_B})

    assert script.main(["--refetch", "--db", str(db_path)], fetcher=fetcher) == 0

    with sessions() as db:
        dirty, clean = list(db.scalars(select(Policy).order_by(Policy.id)))
        assert dirty.content_text == EXPECTED_REFETCH_BODY
        assert dirty.content_hash == hashlib.sha256(EXPECTED_REFETCH_BODY.encode("utf-8")).hexdigest()
        assert "\n" in dirty.content_text  # multi-paragraph (segmented display)
        for chrome in (
            "新华网 >",
            "来源：",
            "阅读下一篇",
            "【纠错】",
            "责任编辑",
            "字体：",
            "分享到：",
        ):
            assert chrome not in dirty.content_text
        # The already-clean flat row re-fetches to the same single-paragraph body.
        assert clean.content_text == EXPECTED_CLEAN


def test_refetch_falls_back_to_flat_clean_when_fetch_fails(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    fetcher = _FakeFetcher({URL_B: DETAIL_HTML_B}, error_urls={URL_A})

    assert script.main(["--refetch", "--db", str(db_path)], fetcher=fetcher) == 0

    with sessions() as db:
        dirty = db.scalar(select(Policy).where(Policy.canonical_url.contains("/a.html")))
        assert dirty is not None
        # Fetch failed -> fallback flat _clean_content; DIRTY_BODY's header sits at
        # line start so it IS stripped here, but the body stays single-line.
        assert dirty.content_text == EXPECTED_CLEAN
        assert "新华网 >" not in dirty.content_text
        assert "\n" not in dirty.content_text


def test_refetch_falls_back_when_no_p_blocks(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    fetcher = _FakeFetcher(
        {URL_A: "<html><body><div>no paragraphs here</div></body></html>", URL_B: DETAIL_HTML_B}
    )

    assert script.main(["--refetch", "--db", str(db_path)], fetcher=fetcher) == 0

    with sessions() as db:
        dirty = db.scalar(select(Policy).where(Policy.canonical_url.contains("/a.html")))
        assert dirty is not None
        assert dirty.content_text == EXPECTED_CLEAN  # flat fallback


def test_refetch_dry_run_does_not_write(reclean_db, capsys: pytest.CaptureFixture[str]) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    fetcher = _FakeFetcher({URL_A: DETAIL_HTML_A, URL_B: DETAIL_HTML_B})

    assert script.main(["--refetch", "--dry-run", "--db", str(db_path)], fetcher=fetcher) == 0
    out = capsys.readouterr().out
    assert "[dry-run][重抓分段]" in out
    assert "重抓" in out

    with sessions() as db:
        dirty = db.scalar(select(Policy).where(Policy.canonical_url.contains("/a.html")))
        assert dirty is not None
        assert dirty.content_text == DIRTY_BODY  # nothing written
        assert dirty.content_hash == "stale-hash-value"


def test_refetch_is_idempotent(reclean_db) -> None:
    db_path, sessions = reclean_db
    script = _load_script()
    fetcher = _FakeFetcher({URL_A: DETAIL_HTML_A, URL_B: DETAIL_HTML_B})

    assert script.main(["--refetch", "--db", str(db_path)], fetcher=fetcher) == 0
    before = _rows(sessions)
    assert script.main(["--refetch", "--db", str(db_path)], fetcher=fetcher) == 0
    assert _rows(sessions) == before


def test_refetch_requires_webfetch_config_when_no_fetcher(
    reclean_db, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path, _sessions = reclean_db
    script = _load_script()
    monkeypatch.setenv("POLICY_ANALYSIS_WEBFETCH__BASE_URL", "")
    monkeypatch.setenv("POLICY_ANALYSIS_WEBFETCH__API_KEY", "")

    assert script.main(["--refetch", "--db", str(db_path)]) == 1
    assert "WebFetch" in capsys.readouterr().err
