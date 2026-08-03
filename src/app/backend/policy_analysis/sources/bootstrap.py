"""Strict, offline loading and idempotent import of packaged seed URLs."""

from __future__ import annotations

import json
import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError

from policy_analysis.sources.schemas import SeedImportResult, SeedUrlImport
from policy_analysis.sources.service import SourceService
from policy_analysis.sources.url_validation import normalized_http_hostname

_RESOURCE_PACKAGE = "policy_analysis.collectors.resources"
_RESOURCE_NAME = "xinhua_politburo_seed_urls.json"
_ALLOWED_HOSTS = frozenset({"news.cn", "www.news.cn", "xinhuanet.com", "www.xinhuanet.com"})
_REQUIRED_TITLE_PREFIX = "中共中央政治局召开会议"
_EARLIEST_DATE = date(2021, 8, 1)
_LATEST_DATE = date(2026, 8, 1)
_OLD_URL_DATE = re.compile(r"/(20\d{2})-(\d{2})/(\d{2})(?:/|$)")
_CURRENT_URL_DATE = re.compile(r"/(20\d{2})(\d{2})(\d{2})(?:/|$)")
_MANIFEST_ADAPTER = TypeAdapter(list[SeedUrlImport])


class SeedManifestError(ValueError):
    """A stable error for an unreadable or invalid all-or-nothing manifest."""


def load_seed_manifest(path: Path | None = None) -> tuple[SeedUrlImport, ...]:
    """Load and strictly validate a packaged or explicitly supplied manifest."""

    try:
        payload = _read_manifest(path)
        raw_entries = _parse_raw_manifest(payload)
        entries = _MANIFEST_ADAPTER.validate_json(payload)
        _validate_raw_values(raw_entries, entries)
        _validate_entries(entries)
    except (ImportError, OSError, UnicodeError, ValidationError, ValueError):
        raise SeedManifestError("seed manifest invalid") from None
    return tuple(entries)


def import_seed_manifest(
    source_service: SourceService,
    rule_id: int,
    path: Path | None = None,
) -> SeedImportResult:
    """Load the entire manifest, then delegate its idempotent transaction."""

    entries = load_seed_manifest(path)
    return source_service.import_seed_urls(rule_id, entries)


def _read_manifest(path: Path | None) -> bytes:
    if path is not None:
        return path.read_bytes().decode("utf-8").encode("utf-8")
    resource = resources.files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME)
    return resource.read_bytes().decode("utf-8").encode("utf-8")


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


def _validate_entries(entries: list[SeedUrlImport]) -> None:
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
            not entry.expected_title.startswith(_REQUIRED_TITLE_PREFIX)
            or entry.expected_title.endswith("-新华网")
            or entry.is_verified is not True
            or not _EARLIEST_DATE <= entry.expected_published_date <= _LATEST_DATE
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
        or host not in _ALLOWED_HOSTS
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
    for pattern in (_OLD_URL_DATE, _CURRENT_URL_DATE):
        match = pattern.search(url)
        if match is not None:
            try:
                return date(*(int(part) for part in match.groups()))
            except ValueError:
                break
    raise ValueError("manifest URL has no valid publication date")
