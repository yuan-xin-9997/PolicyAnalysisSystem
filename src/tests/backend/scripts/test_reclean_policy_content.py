from __future__ import annotations

import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
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
