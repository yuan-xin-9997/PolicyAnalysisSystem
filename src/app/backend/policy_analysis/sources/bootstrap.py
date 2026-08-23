"""Strict, offline loading and idempotent import of packaged seed URLs.

Each collection scenario is described by a :class:`ScenarioSpec` that binds
together its policy category, source/rule defaults, seed manifest resource,
and validation rules. ``load_seed_manifest`` and ``bootstrap_default_catalog``
operate uniformly over the registered scenarios, so a new rule can be added by
introducing a single :class:`ScenarioSpec` -- no other code changes required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.sources.models import CollectionRule, PolicyCategory, Source
from policy_analysis.sources.schemas import SeedImportResult, SeedUrlImport
from policy_analysis.sources.service import SourceService
from policy_analysis.sources.url_validation import normalized_http_hostname

_RESOURCE_PACKAGE = "policy_analysis.collectors.resources"

# Base registrable domains whose subdomains all belong to the same publisher:
# e.g. politics.people.com.cn / cpc.people.com.cn / m.people.com.cn are all
# People's Daily, news.cctv.com / tv.cctv.com are all CCTV. A host is allowed
# when it equals one of these domains or is a direct/deeper subdomain of it.
_ALLOWED_BASE_DOMAINS = frozenset(
    {
        "news.cn",
        "xinhuanet.com",
        "people.com.cn",
        "cctv.com",
    }
)


def _host_allowed(host: str) -> bool:
    return any(host == base or host.endswith(f".{base}") for base in _ALLOWED_BASE_DOMAINS)


_OLD_URL_DATE = re.compile(r"/(20\d{2})-(\d{2})/(\d{2})(?:/|$)")
_CURRENT_URL_DATE = re.compile(r"/(20\d{2})(\d{2})(\d{2})(?:/|$)")
_PEOPLE_CN_URL_DATE = re.compile(r"/n1/(20\d{2})/(\d{2})(\d{2})(?:/|$)")
_CCTV_URL_DATE = re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/(?:ART|VIDE)")

_MANIFEST_ADAPTER = TypeAdapter(list[SeedUrlImport])

_DEFAULT_SOURCE_CODE = "xinhua"
_DEFAULT_SOURCE_NAME = "新华网"
_DEFAULT_SOURCE_ORGANIZATION = "新华社"
_DEFAULT_SOURCE_BASE_URL = "https://news.cn/"
_DEFAULT_SOURCE_ADAPTER = "xinhua"
_DEFAULT_ALLOWED_DOMAINS = [
    "news.cn",
    "www.news.cn",
    "xinhuanet.com",
    "www.xinhuanet.com",
    "people.com.cn",
    "cctv.com",
]
_DEFAULT_EXCLUDE_KEYWORDS = ["视频"]
_DEFAULT_DISCOVERY = {
    "rss_urls": ["https://www.news.cn/rss/politics.xml"],
    "channel_urls": ["https://www.news.cn/politics/leaders/index.htm"],
}


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """A single collection scenario: how to validate, seed, and bootstrap it."""

    key: str
    resource_name: str
    category_code: str
    category_name: str
    category_description: str
    rule_name: str
    include_keywords: tuple[str, ...]
    history_years: int
    earliest_date: date
    latest_date: date
    title_must_contain: tuple[str, ...]
    title_must_not_end_with: tuple[str, ...] = ("-新华网",)


POLITBURO_SPEC = ScenarioSpec(
    key="politburo",
    resource_name="xinhua_politburo_seed_urls.json",
    category_code="politburo_meeting",
    category_name="中央政治局会议",
    category_description="新华社中央政治局会议通报",
    rule_name="中央政治局会议",
    include_keywords=("中共中央政治局召开会议",),
    history_years=5,
    earliest_date=date(2021, 8, 1),
    latest_date=date(2026, 8, 1),
    title_must_contain=("中共中央政治局召开会议",),
)

FINANCE_COUNCIL_SPEC = ScenarioSpec(
    key="finance_council",
    resource_name="xinhua_finance_council_seed_urls.json",
    category_code="finance_council_meeting",
    category_name="中央财经委员会会议",
    category_description="新华社中央财经委员会会议通报",
    rule_name="中央财经委员会会议",
    include_keywords=("中央财经委员会",),
    history_years=9,
    earliest_date=date(2018, 4, 1),
    latest_date=date(2026, 8, 1),
    title_must_contain=("中央财经委员会", "国家中长期经济社会发展战略若干重大问题"),
)

DEFAULT_SCENARIOS: tuple[ScenarioSpec, ...] = (POLITBURO_SPEC, FINANCE_COUNCIL_SPEC)
DEFAULT_SCENARIO = POLITBURO_SPEC


class SeedManifestError(ValueError):
    """A stable error for an unreadable or invalid all-or-nothing manifest."""


def load_seed_manifest(
    path: Path | None = None,
    spec: ScenarioSpec = DEFAULT_SCENARIO,
) -> tuple[SeedUrlImport, ...]:
    """Load and strictly validate a packaged or explicitly supplied manifest."""

    try:
        payload = _read_manifest(path, spec)
        raw_entries = _parse_raw_manifest(payload)
        entries = _MANIFEST_ADAPTER.validate_json(payload)
        _validate_raw_values(raw_entries, entries)
        _validate_entries(entries, spec)
    except (ImportError, OSError, UnicodeError, ValidationError, ValueError):
        raise SeedManifestError("seed manifest invalid") from None
    return tuple(entries)


def import_seed_manifest(
    source_service: SourceService,
    rule_id: int,
    path: Path | None = None,
    spec: ScenarioSpec = DEFAULT_SCENARIO,
) -> SeedImportResult:
    """Load the entire manifest, then delegate its idempotent transaction."""

    entries = load_seed_manifest(path, spec)
    return source_service.import_seed_urls(rule_id, entries)


def bootstrap_default_catalog(sessions: sessionmaker[Session]) -> dict[str, SeedImportResult]:
    """Ensure every default scenario has a category, source, rule, and seed manifest."""

    results: dict[str, SeedImportResult] = {}
    for spec in DEFAULT_SCENARIOS:
        with sessions.begin() as session:
            category = _ensure_category(session, spec)
            source = _ensure_default_source(session)
            session.flush()
            rule = _ensure_rule(session, source, category, spec)
            session.flush()
            rule_id = rule.id
        results[spec.key] = import_seed_manifest(SourceService(sessions), rule_id, spec=spec)
    return results


def _read_manifest(path: Path | None, spec: ScenarioSpec) -> bytes:
    if path is not None:
        return path.read_bytes().decode("utf-8").encode("utf-8")
    resource = resources.files(_RESOURCE_PACKAGE).joinpath(spec.resource_name)
    return resource.read_bytes().decode("utf-8").encode("utf-8")


def _ensure_category(session: Session, spec: ScenarioSpec) -> PolicyCategory:
    category = session.scalar(select(PolicyCategory).where(PolicyCategory.code == spec.category_code))
    if category is None:
        category = PolicyCategory(
            code=spec.category_code,
            name=spec.category_name,
            description=spec.category_description,
            is_active=True,
        )
        session.add(category)
    return category


def _ensure_default_source(session: Session) -> Source:
    source = session.scalar(select(Source).where(Source.code == _DEFAULT_SOURCE_CODE))
    if source is None:
        source = Source(
            code=_DEFAULT_SOURCE_CODE,
            name=_DEFAULT_SOURCE_NAME,
            organization=_DEFAULT_SOURCE_ORGANIZATION,
            base_url=_DEFAULT_SOURCE_BASE_URL,
            adapter_type=_DEFAULT_SOURCE_ADAPTER,
            allowed_domains_json=_encode_json(_DEFAULT_ALLOWED_DOMAINS),
            is_active=True,
        )
        session.add(source)
    return source


def _ensure_rule(
    session: Session, source: Source, category: PolicyCategory, spec: ScenarioSpec
) -> CollectionRule:
    rule = session.scalar(
        select(CollectionRule)
        .where(CollectionRule.name == spec.rule_name)
        .where(CollectionRule.source_id == source.id)
        .where(CollectionRule.category_id == category.id)
    )
    if rule is None:
        rule = CollectionRule(
            source=source,
            category=category,
            name=spec.rule_name,
            include_keywords_json=_encode_json(list(spec.include_keywords)),
            exclude_keywords_json=_encode_json(_DEFAULT_EXCLUDE_KEYWORDS),
            history_years=spec.history_years,
            discovery_config_json=_encode_json(_DEFAULT_DISCOVERY),
            is_active=True,
        )
        session.add(rule)
    return rule


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_raw_manifest(payload: bytes) -> list[Any]:
    value = json.loads(payload, object_pairs_hook=_unique_object)
    if not isinstance(value, list):
        raise ValueError("manifest root must be a list")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _validate_raw_values(raw_entries: list[Any], entries: list[SeedUrlImport]) -> None:
    if len(raw_entries) != len(entries):
        raise ValueError("manifest item count changed during validation")
    for raw, entry in zip(raw_entries, entries, strict=True):
        if not isinstance(raw, dict) or (
            raw.get("url") != entry.url
            or raw.get("expected_title") != entry.expected_title
            or raw.get("expected_published_date") != entry.expected_published_date.isoformat()
            or raw.get("is_verified") is not True
        ):
            raise ValueError("manifest values must already be canonical")


def _validate_entries(entries: list[SeedUrlImport], spec: ScenarioSpec) -> None:
    if not entries:
        raise ValueError("empty manifest")

    urls: set[str] = set()
    canonical_urls: set[str] = set()
    keys: list[tuple[date, str]] = []
    for entry in entries:
        canonical = _validate_url(entry.url)
        if entry.url in urls or canonical in canonical_urls:
            raise ValueError("duplicate URL")
        urls.add(entry.url)
        canonical_urls.add(canonical)

        if (
            not any(token in entry.expected_title for token in spec.title_must_contain)
            or any(entry.expected_title.endswith(suffix) for suffix in spec.title_must_not_end_with)
            or entry.is_verified is not True
            or not spec.earliest_date <= entry.expected_published_date <= spec.latest_date
            or _url_date(canonical) != entry.expected_published_date
        ):
            raise ValueError("invalid manifest entry")
        keys.append((entry.expected_published_date, entry.url))

    if keys != sorted(keys):
        raise ValueError("manifest is not sorted")


def _validate_url(url: str) -> str:
    parsed = urlsplit(url)
    host = normalized_http_hostname(url)
    if (
        parsed.scheme != "https"
        or not _host_allowed(host)
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid manifest URL")
    canonical = urlunsplit(("https", host, parsed.path or "/", "", ""))
    if canonical != url:
        raise ValueError("manifest URL is not canonical")
    return canonical


def _url_date(url: str) -> date:
    for pattern in (
        _OLD_URL_DATE,
        _CURRENT_URL_DATE,
        _PEOPLE_CN_URL_DATE,
        _CCTV_URL_DATE,
    ):
        match = pattern.search(url)
        if match is not None:
            try:
                return date(*(int(part) for part in match.groups()))
            except ValueError:
                break
    raise ValueError("manifest URL has no valid publication date")
