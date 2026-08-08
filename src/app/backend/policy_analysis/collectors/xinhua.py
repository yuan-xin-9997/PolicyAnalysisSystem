"""Pure Xinhua discovery, URL normalization, and article classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from policy_analysis.collectors.base import DiscoveredLink, ExtractedArticle
from policy_analysis.sources.url_validation import normalize_dns_name, normalized_http_hostname

BEIJING = ZoneInfo("Asia/Shanghai")
TRACKING_PARAMETERS = frozenset({"from", "spm", "utm_campaign", "utm_medium", "utm_source"})
_VIDEO_MARKERS = ("编导：", "音视频部制作")
_LEAD_LIMIT = 1000
_ISO_TIMESTAMP = re.compile(
    r"(?<!\d)20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})(?!\d)"
)
_DASHED_DATE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?"
)
_CHINESE_DATE = re.compile(
    r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})日"
    r"(?:\s*(\d{1,2})[:时](\d{1,2})(?:[:分](\d{1,2}))?秒?)?"
)
_OLD_URL_DATE = re.compile(r"/(20\d{2})-(\d{2})/(\d{2})(?:/|$)")
_CURRENT_URL_DATE = re.compile(r"/(20\d{2})(\d{2})(\d{2})(?:/|$)")

# Patterns for stripping webpage page-chrome from extracted article bodies.
# A decorative header segment is one of: a ">"-separated breadcrumb, a leading
# timestamp, or a "来源：xxx" source marker -- the page header Xinhua prepends
# (e.g. "新华网 > 时政 > 正文 2026 07/30 14:36:12 来源：新华网").
_BREADCRUMB_SEGMENT = r"(?:[^\s>]+\s*>\s*)+[^\s>]+"
_TIMESTAMP_SEGMENT = (
    r"\d{4}(?:[-/年.]\d{1,2}[-/月.]\d{1,2}日?|\s\d{1,2}/\d{1,2})"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
)
_SOURCE_SEGMENT = r"来源：[^\s]+"
_DECORATIVE_SEGMENT = rf"(?:{_BREADCRUMB_SEGMENT}|{_TIMESTAMP_SEGMENT}|{_SOURCE_SEGMENT})"
_DECORATIVE_LINE = re.compile(rf"^(?:{_DECORATIVE_SEGMENT}\s*)+$")
_DECORATIVE_PREFIX = re.compile(rf"^\s*(?:{_DECORATIVE_SEGMENT}\s*)+")
_READ_NEXT_MARKER = "阅读下一篇"


@dataclass(frozen=True, slots=True)
class Classification:
    accepted: bool
    reason_code: str
    canonical_url: str
    title: str
    content: str
    publisher: str
    published_at: datetime | None
    artifact_id: str


class XinhuaCollector:
    """Discover and classify Xinhua links without performing any I/O."""

    def __init__(
        self,
        allowed_domains: set[str],
        include_keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        minimum_content_chars: int,
    ) -> None:
        self.allowed_domains = _normalized_domains(allowed_domains)
        self.include_keywords = _normalized_keywords(
            include_keywords,
            require_nonempty=True,
        )
        self.exclude_keywords = _normalized_keywords(
            exclude_keywords,
            require_nonempty=False,
        )
        if (
            isinstance(minimum_content_chars, bool)
            or not isinstance(minimum_content_chars, int)
            or minimum_content_chars <= 0
        ):
            raise ValueError("MINIMUM_CONTENT_CHARS_INVALID")
        self.minimum_content_chars = minimum_content_chars

    def canonicalize(self, url: str) -> str:
        """Return a stable HTTP(S) URL or a single sanitized error."""

        try:
            if not isinstance(url, str):
                raise ValueError
            hostname = normalized_http_hostname(url)
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            port = parts.port
            query = urlencode(
                [
                    (key, value)
                    for key, value in parse_qsl(parts.query, keep_blank_values=True)
                    if key.lower() not in TRACKING_PARAMETERS
                ]
            )
        except (AttributeError, TypeError, UnicodeError, ValueError):
            raise ValueError("URL_INVALID") from None

        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = hostname if port is None or default_port else f"{hostname}:{port}"
        return urlunsplit((scheme, netloc, parts.path or "/", query, ""))

    def discover_from_rss(self, xml_text: str, origin: str) -> list[DiscoveredLink]:
        """Discover matching links from an untrusted RSS document."""

        try:
            root = ElementTree.fromstring(
                xml_text,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
        except (DefusedXmlException, ParseError, TypeError, ValueError):
            raise ValueError("RSS_INVALID") from None

        discovered: list[DiscoveredLink] = []
        seen: set[str] = set()
        for item in root.findall(".//item"):
            url = item.findtext("link")
            text = item.findtext("title")
            if not isinstance(url, str) or not isinstance(text, str):
                continue
            self._append_candidate(discovered, seen, url, text, origin)
        return discovered

    def discover_from_links(
        self,
        links: list[dict[str, str]],
        origin: str,
    ) -> list[DiscoveredLink]:
        """Discover matching links from a WebFetch link-list result."""

        discovered: list[DiscoveredLink] = []
        seen: set[str] = set()
        for link in links:
            if not isinstance(link, dict):
                continue
            url = link.get("href")
            text = link.get("text")
            if not isinstance(url, str) or not isinstance(text, str):
                continue
            self._append_candidate(discovered, seen, url, text, origin)
        return discovered

    def classify(
        self,
        url: str,
        article: ExtractedArticle,
        cutoff: datetime,
    ) -> Classification:
        """Classify an extracted article with a stable first-failure reason."""

        canonical = self.canonicalize(url)
        host = normalized_http_hostname(canonical)
        published_at = self._published_at(article, canonical)
        cutoff_at = _as_beijing(cutoff)
        normalized_title = _collapsed(article.title)
        normalized_content = _collapsed(article.content)
        lead = normalized_content[:_LEAD_LIMIT]
        publisher = _publisher(article.author, normalized_content)

        if host not in self.allowed_domains:
            reason = "DOMAIN_NOT_ALLOWED"
        elif not any(keyword in normalized_title for keyword in self.include_keywords):
            reason = "TITLE_NOT_MATCHED"
        elif any(keyword in normalized_title for keyword in self.exclude_keywords):
            reason = "EXCLUDED_KEYWORD"
        elif len(normalized_content) < self.minimum_content_chars and any(
            marker in normalized_content for marker in _VIDEO_MARKERS
        ):
            reason = "VIDEO_ONLY"
        elif "中共中央政治局" not in lead or "召开会议" not in lead:
            reason = "LEAD_NOT_MATCHED"
        elif not _is_official_source(article.author, normalized_content):
            reason = "SOURCE_NOT_OFFICIAL"
        elif published_at is None:
            reason = "PUBLISHED_AT_MISSING"
        elif published_at < cutoff_at:
            reason = "OUTSIDE_WINDOW"
        elif len(normalized_content) < self.minimum_content_chars:
            reason = "CONTENT_TOO_SHORT"
        else:
            reason = "ACCEPTED"

        return Classification(
            accepted=reason == "ACCEPTED",
            reason_code=reason,
            canonical_url=canonical,
            title=article.title,
            content=_clean_content(article.content),
            publisher=publisher,
            published_at=published_at,
            artifact_id=article.artifact_id,
        )

    @staticmethod
    def _published_at(article: ExtractedArticle, canonical_url: str) -> datetime | None:
        parsed_hint = _first_datetime(article.published_hint)
        if parsed_hint is not None:
            return parsed_hint

        parsed_content = _first_datetime(article.content)
        if parsed_content is not None:
            return parsed_content

        for pattern in (_OLD_URL_DATE, _CURRENT_URL_DATE):
            for match in pattern.finditer(canonical_url):
                parsed_url = _datetime_from_groups(match.groups())
                if parsed_url is not None:
                    return parsed_url
        return None

    def _append_candidate(
        self,
        discovered: list[DiscoveredLink],
        seen: set[str],
        raw_url: str,
        raw_text: str,
        origin: str,
    ) -> None:
        text = _collapsed(raw_text)
        if (
            not text
            or not any(keyword in text for keyword in self.include_keywords)
            or any(keyword in text for keyword in self.exclude_keywords)
        ):
            return

        candidate = self._absolute_candidate(raw_url, origin)
        if candidate is None:
            return
        try:
            canonical = self.canonicalize(candidate)
            host = normalized_http_hostname(canonical)
        except ValueError:
            return
        if host not in self.allowed_domains or canonical in seen:
            return
        seen.add(canonical)
        discovered.append(DiscoveredLink(canonical, text, origin))

    def _absolute_candidate(self, raw_url: str, origin: str) -> str | None:
        candidate = raw_url.strip()
        if not candidate or candidate.startswith(("#", "?")):
            return None
        try:
            parts = urlsplit(candidate)
        except (UnicodeError, ValueError):
            return None
        if parts.scheme:
            return candidate if parts.scheme.lower() in {"http", "https"} else None
        try:
            self.canonicalize(origin)
        except ValueError:
            return None
        return urljoin(origin, candidate)


def _normalized_domains(values: Any) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("ALLOWED_DOMAINS_INVALID")
    try:
        domains = frozenset(normalize_dns_name(value) for value in values)
    except (AttributeError, TypeError, UnicodeError, ValueError):
        raise ValueError("ALLOWED_DOMAINS_INVALID") from None
    if not domains:
        raise ValueError("ALLOWED_DOMAINS_INVALID")
    return domains


def _normalized_keywords(values: Any, *, require_nonempty: bool) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("INCLUDE_KEYWORDS_INVALID" if require_nonempty else "KEYWORD_INVALID")
    try:
        keywords = tuple(value.strip() for value in values)
    except (AttributeError, TypeError):
        raise ValueError("KEYWORD_INVALID") from None
    if require_nonempty and not keywords:
        raise ValueError("INCLUDE_KEYWORDS_INVALID")
    if any(not keyword for keyword in keywords):
        raise ValueError("KEYWORD_INVALID")
    return keywords


def _collapsed(value: str) -> str:
    return " ".join(value.split())


def _clean_content(content: str) -> str:
    """Strip webpage page-chrome from an extracted article body.

    Removes the leading breadcrumb/timestamp/source header, the trailing
    "阅读下一篇" recommendation block, and normalizes paragraph whitespace so
    the stored body is pure policy text. Body-leading dates without a breadcrumb
    or "来源：" marker are preserved (not mistaken for chrome).
    """
    if not isinstance(content, str) or not content:
        return content

    text = content
    marker = text.find(_READ_NEXT_MARKER)
    if marker != -1:
        text = text[:marker]

    lines = text.splitlines()

    # Drop leading blank lines and lines that are purely decorative
    # (breadcrumb/timestamp/source). Skipping blanks first ensures a page
    # header that follows a blank line is still detected and stripped, instead
    # of stopping the scan at the blank and leaving the header in the body.
    start = 0
    while start < len(lines):
        stripped = lines[start].strip()
        if stripped and not _DECORATIVE_LINE.match(stripped):
            break
        start += 1
    lines = lines[start:]

    # Strip an inline decorative prefix from the first remaining line, but only
    # when it carries an unambiguous page-chrome signal (breadcrumb ">" or a
    # "来源：" source marker) so a body-leading date is never removed.
    if lines:
        match = _DECORATIVE_PREFIX.match(lines[0])
        if match and (">" in match.group() or "来源：" in match.group()):
            lines[0] = lines[0][match.end() :]

    # Normalize: strip each line, drop leading/trailing blank lines, collapse
    # runs of blank lines into a single blank line to preserve paragraph breaks.
    normalized: list[str] = []
    prev_blank = True
    for line in lines:
        stripped = line.strip()
        if stripped:
            normalized.append(stripped)
            prev_blank = False
        elif not prev_blank:
            normalized.append("")
            prev_blank = True
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized)


def _is_official_source(author: str, content: str) -> bool:
    return author.strip() in {"新华网", "新华社"} or any(
        marker in content for marker in ("新华社北京", "来源：新华网", "来源：新华社")
    )


def _publisher(author: str, content: str) -> str:
    normalized_author = author.strip()
    if normalized_author in {"新华网", "新华社"}:
        return normalized_author
    if "来源：新华社" in content or "新华社北京" in content:
        return "新华社"
    if "来源：新华网" in content:
        return "新华网"
    return ""


def _first_datetime(value: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None

    exact = _parse_iso_datetime(stripped)
    if exact is not None:
        return _as_beijing(exact)

    candidates: list[tuple[int, int, datetime]] = []
    for match in _ISO_TIMESTAMP.finditer(stripped):
        embedded = _parse_iso_datetime(match.group())
        if embedded is not None:
            candidates.append((match.start(), 0, _as_beijing(embedded)))

    for priority, pattern in enumerate((_DASHED_DATE, _CHINESE_DATE), start=1):
        for match in pattern.finditer(stripped):
            parsed = _datetime_from_groups(match.groups())
            if parsed is not None:
                candidates.append((match.start(), priority, parsed))

    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[:2])[2]


def _datetime_from_groups(groups: tuple[str | None, ...]) -> datetime | None:
    values = [int(value) if value is not None else 0 for value in groups]
    year, month, day = values[:3]
    hour, minute, second = (values[3:] + [0, 0, 0])[:3]
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=BEIJING)
    except ValueError:
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _as_beijing(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("CUTOFF_INVALID")
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)
