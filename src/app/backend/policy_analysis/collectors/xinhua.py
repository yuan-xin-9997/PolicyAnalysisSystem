"""Pure Xinhua discovery, URL normalization, and article classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
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
# A decorative header segment is one of: a ">"-separated breadcrumb (incl. the
# real double-">" variant "新华网 > > 正文"), a leading timestamp (incl. the
# spaced-slash variant "2022 12/ 14 11:37:37"), a "来源：新华社/新华网" source
# marker, or a "字体：小 中 大"/"分享到：" toolbar -- the page header Xinhua
# prepends (e.g. "新华网 > > 正文 2022 12/ 14 11:37:37 来源：新华社").
_BREADCRUMB_SEGMENT = r"(?:[^\s>]*\s*>\s*)+[^\s>]+"
_TIMESTAMP_SEGMENT = (
    r"\d{4}(?:[-/年.]\d{1,2}[-/月.]\d{1,2}日?|\s\d{1,2}/\s*\d{1,2})"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
)
# Bounded to known official source names so a source marker glued to body text
# (no separating space) never eats the leading body paragraph.
_SOURCE_SEGMENT = r"来源：(?:新华社|新华网|新华通讯社)"
_TOOLBAR_SEGMENT = r"字体：\s*小\s*中\s*大|分享到："
_DECORATIVE_SEGMENT = rf"(?:{_BREADCRUMB_SEGMENT}|{_TIMESTAMP_SEGMENT}|{_SOURCE_SEGMENT}|{_TOOLBAR_SEGMENT})"
_DECORATIVE_LINE = re.compile(rf"^(?:{_DECORATIVE_SEGMENT}\s*)+$")
_DECORATIVE_PREFIX = re.compile(rf"^\s*(?:{_DECORATIVE_SEGMENT}\s*)+")
_READ_NEXT_MARKER = "阅读下一篇"
# Trailing pure-numeric tracking ID Xinhua appends (e.g. "01002002011000…").
_TRACKING_ID_TAIL = re.compile(r"\s*\d{16,}\s*$")
_CORRECTION_MARKS = re.compile(r"【纠错】|【责任编辑[:：][^】]*】")
# Inline editor-credit block ("策划：… 新华社音视频部制作 新华通讯社出品") as it
# appears in the flattened single-line body; bounded so it cannot run away.
_CREDIT_BLOCK = re.compile(
    r"(?:策划|监制|制片|统筹|编导|记者|配音)[：:].{0,300}?"
    r"(?:新华社音视频部制作\s*新华通讯社出品|新华社音视频部制作|新华通讯社出品)"
)
# A whole line that is an editor credit (line-based, for paragraph-structured body).
_CREDIT_LINE = re.compile(
    r"^(?:策划|监制|制片|统筹|编导|记者|配音|责任编辑)[：:]"
    r"|^(?:新华社音视频部制作|新华通讯社出品)$"
)
_TOOLBAR_RE = re.compile(_TOOLBAR_SEGMENT)


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
        title = _collapsed(_strip_site_suffix(article.title))
        normalized_title = title
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
        elif not any(keyword in lead for keyword in self.include_keywords):
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
            title=title,
            content=_clean_content(article.content),
            publisher=publisher,
            published_at=published_at,
            artifact_id=article.artifact_id,
        )

    def paragraph_body(self, html: str) -> str:
        """Return the cleaned, paragraph-structured body parsed from raw HTML.

        Parses ``<p>`` blocks (via :func:`extract_paragraphs`) and cleans residual
        page chrome. Returns an empty string when no paragraphs can be extracted,
        signalling the caller to fall back to the inline-cleaned flattened
        article text (``Classification.content``).
        """
        return _clean_content(extract_paragraphs(html))

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


_SITE_TITLE_SUFFIX = re.compile(
    r"\s*[-–—_|｜]{1,2}\s*"
    r"(?:新华网|新华通讯社|人民网|央视网|中国网络电视台|央视国际|中国经济网|光明网|求是网|中国政府网)"
    r"\s*$"
)


def _strip_site_suffix(title: str) -> str:
    """剥离提取标题末尾的站点名装饰。

    网页 ``<title>`` 常带“标题-新华网”之类的站点后缀，而已核验种子
    （seed_urls.expected_title）保存的是纯标题，精确比对前必须先剥离。
    只剥离已知官方站点名单，避免截断真实标题中的分隔符。
    """
    return _SITE_TITLE_SUFFIX.sub("", title, count=1).strip()


def _clean_content(content: str) -> str:
    """Strip webpage page-chrome from an extracted article body.

    Removes the leading breadcrumb/timestamp/source/toolbar header, the trailing
    "阅读下一篇" recommendation block, editor-credit footers ("策划：… 出品"),
    correction markers ("【纠错】"/"【责任编辑:xxx】"), and trailing numeric
    tracking IDs; normalizes paragraph whitespace so the stored body is pure
    policy text. Operates on both paragraph-structured text (newline-separated,
    the primary path) and flattened single-line text (the fallback path) because
    WebFetch's ``generic.article`` adapter joins paragraphs with single spaces.
    Body-leading dates without a breadcrumb or "来源：" marker are preserved
    (not mistaken for chrome).
    """
    if not isinstance(content, str) or not content:
        return content

    text = content
    marker = text.find(_READ_NEXT_MARKER)
    if marker != -1:
        text = text[:marker]

    # Strip decorations that may appear inline within a line (flattened body) or
    # as standalone lines (paragraph-structured body).
    text = _CORRECTION_MARKS.sub(" ", text)
    text = _CREDIT_BLOCK.sub(" ", text)
    text = _TOOLBAR_RE.sub(" ", text)

    lines = text.splitlines()

    # Drop leading blank lines and lines that are purely decorative or editor
    # credits. Skipping blanks first ensures a page header that follows a blank
    # line is still detected and stripped, instead of stopping the scan at the
    # blank and leaving the header in the body.
    start = 0
    while start < len(lines):
        stripped = lines[start].strip()
        if stripped and not _DECORATIVE_LINE.match(stripped) and not _CREDIT_LINE.match(stripped):
            break
        start += 1
    lines = lines[start:]

    # Drop trailing blank/credit-only lines.
    while lines and (not lines[-1].strip() or _CREDIT_LINE.match(lines[-1].strip())):
        lines.pop()

    # Strip an inline decorative prefix from the first remaining line, but only
    # when it carries an unambiguous page-chrome signal (breadcrumb ">", a
    # "来源：" source marker, or a font/share toolbar) so a body-leading date is
    # never removed.
    if lines:
        match = _DECORATIVE_PREFIX.match(lines[0])
        if match and (
            ">" in match.group()
            or "来源：" in match.group()
            or "字体：" in match.group()
            or "分享到：" in match.group()
        ):
            lines[0] = lines[0][match.end() :]

    # Strip a trailing pure-numeric tracking ID from the last line.
    if lines:
        lines[-1] = _TRACKING_ID_TAIL.sub("", lines[-1]).rstrip()

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


class _ParagraphParser(HTMLParser):
    """Collect text from ``<p>`` blocks, preferring the article detail container.

    WebFetch's ``generic.article`` adapter flattens the body into a single
    space-joined line, discarding paragraph boundaries. This parser recovers
    them from the raw HTML by reading ``<p>`` blocks inside ``<div id="detail">``
    (falling back to all ``<p>`` blocks when no detail container is present),
    which also naturally excludes most page chrome that lives outside ``<p>``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._detail: list[str] = []
        self._all: list[str] = []
        self._detail_depth = 0
        self._in_p = False
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered == "div":
            attr_map = {key.casefold(): (value or "") for key, value in attrs}
            if attr_map.get("id") == "detail" or "detail" in attr_map.get("class", ""):
                self._detail_depth = 1
            elif self._detail_depth:
                self._detail_depth += 1
        elif lowered == "p":
            self._in_p = True
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "p" and self._in_p:
            self._in_p = False
            text = " ".join("".join(self._text).split())
            if text:
                self._all.append(text)
                if self._detail_depth:
                    self._detail.append(text)
            self._text = []
        elif lowered == "div" and self._detail_depth:
            self._detail_depth -= 1

    def paragraphs(self) -> list[str]:
        return self._detail if self._detail else self._all


def extract_paragraphs(html: str) -> str:
    """Extract article body paragraphs from raw HTML as newline-joined text.

    Returns ``""`` when no ``<p>`` blocks are found, so callers can fall back to
    the flattened article text. Has no I/O and raises no exception on malformed
    HTML (the stdlib parser is lenient).
    """
    if not isinstance(html, str) or not html:
        return ""
    parser = _ParagraphParser()
    parser.feed(html)
    parser.close()
    return "\n".join(parser.paragraphs())


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
