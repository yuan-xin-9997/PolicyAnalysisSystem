from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import date
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

import policy_analysis.sources.bootstrap as seed_bootstrap
import pytest
from policy_analysis.collectors.xinhua import XinhuaCollector
from policy_analysis.sources.bootstrap import (
    SeedManifestError,
    import_seed_manifest,
    load_seed_manifest,
)
from policy_analysis.sources.models import CollectionRule, PolicyCategory, SeedUrl, Source
from policy_analysis.sources.schemas import CollectionRuleCreate
from policy_analysis.sources.service import SourceService
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

RESOURCE_PACKAGE = "policy_analysis.collectors.resources"
RESOURCE_NAME = "xinhua_politburo_seed_urls.json"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
VALIDATOR_SCRIPT = PROJECT_ROOT / "scripts/validate_seed_manifest.py"
TITLE = "中共中央政治局召开会议 中共中央总书记习近平主持会议"
EXCLUDED_URLS = {
    "https://www.news.cn/politics/leaders/2022-08/30/c_1128962332.htm",
    "https://www.news.cn/2023-11/27/c_1129995281.htm",
    "https://www.news.cn/20260130/e9daba7d39a040b2b52eb85cc1bf894a/c.html",
    "https://www.news.cn/politics/leaders/20260630/01c452e568204589b4dd87d05856692e/c.html",
    "https://www.news.cn/politics/20260730/a462ad8bb56b4e5888727a11b2411999/c.html",
}


def manifest_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "url": "https://www.news.cn/20240131/00000000000000000000000000000000/c.html",
        "expected_title": TITLE,
        "expected_published_date": "2024-01-31",
        "is_verified": True,
    }
    entry.update(overrides)
    return entry


def write_manifest(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_default_seed_manifest_has_verified_five_year_inventory() -> None:
    entries = load_seed_manifest()

    assert isinstance(entries, tuple)
    assert len(entries) == 51
    assert Counter(entry.expected_published_date.year for entry in entries) == {
        2021: 4,
        2022: 9,
        2023: 10,
        2024: 11,
        2025: 11,
        2026: 6,
    }
    assert entries[0].expected_published_date.isoformat() == "2021-08-31"
    assert entries[-1].expected_published_date.isoformat() == "2026-07-30"
    assert (
        sum(
            entry.expected_published_date.year == 2025 and entry.expected_published_date.month == 12
            for entry in entries
        )
        == 2
    )
    assert all(entry.is_verified for entry in entries)


def test_default_manifest_is_packaged_and_fulfils_every_semantic_constraint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    resource = resources.files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME)
    assert resource.is_file()
    assert resource.read_bytes().endswith(b"\n")

    entries = load_seed_manifest()
    urls = [entry.url for entry in entries]
    keys = [(entry.expected_published_date, entry.url) for entry in entries]
    collector = XinhuaCollector(
        allowed_domains={"news.cn", "www.news.cn", "xinhuanet.com", "www.xinhuanet.com"},
        include_keywords=("中共中央政治局召开会议",),
        exclude_keywords=(),
        minimum_content_chars=1,
    )

    assert urls == list(dict.fromkeys(urls))
    assert keys == sorted(keys)
    assert not EXCLUDED_URLS.intersection(urls)
    assert all(entry.expected_title.startswith("中共中央政治局召开会议") for entry in entries)
    assert all(not entry.expected_title.endswith("-新华网") for entry in entries)
    assert all(urlsplit(url).hostname in collector.allowed_domains for url in urls)
    assert all(collector.canonicalize(url) == url for url in urls)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="root-object"),
        pytest.param([], id="empty-root"),
        pytest.param([{"url": "https://www.news.cn/20240131/item/c.html"}], id="missing-fields"),
        pytest.param([manifest_entry(extra="forbidden")], id="extra-field"),
        pytest.param([manifest_entry(expected_published_date=20240131)], id="date-wrong-type"),
        pytest.param([manifest_entry(is_verified=1)], id="boolean-wrong-type"),
        pytest.param([manifest_entry(is_verified=False)], id="unverified"),
        pytest.param(
            [manifest_entry(url="https://politics.news.cn/20240131/item/c.html")],
            id="nonofficial-subdomain",
        ),
        pytest.param(
            [manifest_entry(url="http://www.news.cn/20240131/item/c.html")],
            id="http",
        ),
        pytest.param(
            [manifest_entry(url="https://www.news.cn/20240131/item/c.html?token=secret")],
            id="query",
        ),
        pytest.param(
            [manifest_entry(url="https://www.news.cn/20240131/item/c.html#section")],
            id="fragment",
        ),
        pytest.param(
            [manifest_entry(url="https://www.news.cn:443/20240131/item/c.html")],
            id="explicit-default-port",
        ),
        pytest.param(
            [manifest_entry(url=" https://www.news.cn/20240131/item/c.html")],
            id="url-leading-whitespace",
        ),
        pytest.param([manifest_entry(expected_title="中共中央政治局会议建议召开全会")], id="title"),
        pytest.param([manifest_entry(expected_title=f"{TITLE} ")], id="title-trailing-whitespace"),
        pytest.param([manifest_entry(expected_published_date="2021-07-31")], id="date-range"),
        pytest.param([manifest_entry(expected_published_date="2024-02-01")], id="date-mismatch"),
        pytest.param(
            [
                manifest_entry(
                    url="https://www.news.cn/20240229/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/c.html",
                    expected_published_date="2024-02-29",
                ),
                manifest_entry(),
            ],
            id="unsorted",
        ),
        pytest.param([manifest_entry(), manifest_entry()], id="duplicate"),
    ],
)
def test_invalid_manifests_are_rejected_without_leaking_input(
    tmp_path: Path,
    payload: object,
) -> None:
    secret = "token=secret"
    path = write_manifest(tmp_path / "sensitive-absolute-name.json", payload)

    with pytest.raises(SeedManifestError) as caught:
        load_seed_manifest(path)

    message = str(caught.value)
    assert secret not in message
    assert str(tmp_path) not in message
    assert "http" not in message.lower()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"not-json", id="malformed-json"),
        pytest.param(b"\xff\xfe", id="non-utf8"),
    ],
)
def test_unreadable_manifest_content_has_a_controlled_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(content)

    with pytest.raises(SeedManifestError, match=r"^seed manifest invalid$"):
        load_seed_manifest(path)


def test_missing_manifest_has_a_controlled_error(tmp_path: Path) -> None:
    with pytest.raises(SeedManifestError, match=r"^seed manifest invalid$"):
        load_seed_manifest(tmp_path / "missing.json")


def test_missing_packaged_resource_has_a_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_package(_package: str) -> object:
        raise ModuleNotFoundError("sensitive internal package name")

    monkeypatch.setattr(seed_bootstrap.resources, "files", missing_package)

    with pytest.raises(SeedManifestError, match=r"^seed manifest invalid$"):
        load_seed_manifest()


def test_duplicate_json_object_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-key.json"
    path.write_text(
        '[{"url":"https://www.news.cn/20240131/one/c.html",'
        '"url":"https://www.news.cn/20240131/two/c.html",'
        f'"expected_title":"{TITLE}",'
        '"expected_published_date":"2024-01-31","is_verified":true}]',
        encoding="utf-8",
    )

    with pytest.raises(SeedManifestError, match=r"^seed manifest invalid$"):
        load_seed_manifest(path)


@pytest.fixture
def manifest_rule(
    database_sessions: sessionmaker[Session],
) -> tuple[SourceService, int]:
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

    service = SourceService(database_sessions)
    rule = service.create_rule(
        CollectionRuleCreate.model_validate(
            {
                "name": "中央政治局会议",
                "source_code": "xinhua",
                "category_code": "politburo_meeting",
                "include_keywords": ["中共中央政治局召开会议"],
                "exclude_keywords": ["视频"],
                "history_years": 5,
                "discovery": {
                    "rss_urls": ["https://www.news.cn/politics/news_politics.xml"],
                    "channel_urls": ["https://www.news.cn/politics/leaders/"],
                },
            }
        )
    )
    return service, rule.id


def test_manifest_import_is_idempotent_and_upgrade_preserves_site_data(
    manifest_rule: tuple[SourceService, int],
    database_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    service, rule_id = manifest_rule

    first = import_seed_manifest(service, rule_id)
    second = import_seed_manifest(service, rule_id)
    assert (first.inserted, first.existing) == (51, 0)
    assert (second.inserted, second.existing) == (0, 51)

    original_url = load_seed_manifest()[0].url
    site_url = "https://www.news.cn/site-added/c.html"
    with database_sessions.begin() as database:
        stored = database.scalar(
            select(SeedUrl).where(SeedUrl.rule_id == rule_id, SeedUrl.url == original_url)
        )
        assert stored is not None
        stored.expected_title = "现场人工标题"
        stored.expected_published_date = date(2021, 8, 30)
        stored.is_verified = False
        database.add(
            SeedUrl(
                rule_id=rule_id,
                url=site_url,
                expected_title="现场新增种子",
                expected_published_date=date(2026, 7, 31),
                is_verified=True,
            )
        )

    upgraded = [entry.model_dump(mode="json") for entry in load_seed_manifest()]
    upgraded[0]["expected_title"] = "中共中央政治局召开会议 资源升级标题"
    upgraded.append(
        manifest_entry(
            url="https://www.news.cn/20260731/00000000000000000000000000000000/c.html",
            expected_published_date="2026-07-31",
        )
    )
    upgraded_path = write_manifest(tmp_path / "upgraded.json", upgraded)

    result = import_seed_manifest(service, rule_id, upgraded_path)
    assert (result.inserted, result.existing) == (1, 51)

    with database_sessions() as database:
        rows = list(database.scalars(select(SeedUrl).where(SeedUrl.rule_id == rule_id)))
        assert len(rows) == 53
        assert {row.url for row in rows}.issuperset({original_url, site_url})
        preserved = next(row for row in rows if row.url == original_url)
        assert preserved.expected_title == "现场人工标题"
        assert preserved.expected_published_date == date(2021, 8, 30)
        assert preserved.is_verified is False


def test_invalid_manifest_fails_before_database_write(
    manifest_rule: tuple[SourceService, int],
    database_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    service, rule_id = manifest_rule
    invalid = write_manifest(tmp_path / "invalid.json", [manifest_entry(is_verified=False)])

    with pytest.raises(SeedManifestError):
        import_seed_manifest(service, rule_id, invalid)

    with database_sessions() as database:
        assert database.scalar(select(func.count()).select_from(SeedUrl)) == 0
        assert database.scalar(select(CollectionRule.id).where(CollectionRule.id == rule_id)) == rule_id


def test_validator_script_runs_offline_from_an_unrelated_working_directory(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_SCRIPT)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == "seed manifest valid: 0 invalid, 0 duplicate\n"
    assert completed.stderr == ""


def test_load_seed_manifest_supports_finance_council_scenario() -> None:
    from policy_analysis.sources.bootstrap import FINANCE_COUNCIL_SPEC, load_seed_manifest

    entries = load_seed_manifest(spec=FINANCE_COUNCIL_SPEC)

    assert entries
    assert all("中央财经委员会" in entry.expected_title for entry in entries)
    assert all(entry.is_verified for entry in entries)
    dates = [entry.expected_published_date for entry in entries]
    assert dates == sorted(dates)
    assert min(dates) >= date(2018, 4, 1)
    assert max(dates) <= date(2026, 8, 1)
