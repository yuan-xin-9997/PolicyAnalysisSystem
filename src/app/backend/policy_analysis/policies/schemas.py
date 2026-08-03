"""Strict write, query, and public response models for policies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from policy_analysis.sources.url_validation import normalized_http_hostname

PositiveId = Annotated[int, Field(strict=True, ge=1)]
BoundedTitle = Annotated[str, StringConstraints(min_length=1, max_length=512)]
BoundedPublisher = Annotated[str, StringConstraints(min_length=1, max_length=256)]
BoundedHash = Annotated[str, StringConstraints(min_length=1, max_length=128)]
BoundedArtifactId = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PolicyWrite(StrictModel):
    """The only accepted runner-to-policy-service write boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: PositiveId
    category_id: PositiveId
    title: BoundedTitle
    canonical_url: Annotated[HttpUrl, Field(max_length=2048)]
    publisher: BoundedPublisher
    published_at: datetime
    content_text: Annotated[str, StringConstraints(min_length=1)]
    content_hash: BoundedHash
    webfetch_artifact_id: BoundedArtifactId
    crawled_at: datetime

    @field_validator("title", "publisher", "content_hash", "webfetch_artifact_id")
    @classmethod
    def reject_ambiguous_bounded_strings(cls, value: str) -> str:
        if value != value.strip() or any(_is_control(character) for character in value):
            raise ValueError("字符串值必须为无控制字符的规范文本")
        return value

    @field_validator("content_text")
    @classmethod
    def validate_plain_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("正文必须为非空纯文本")
        return value

    @field_validator("canonical_url", mode="before")
    @classmethod
    def validate_canonical_url_input(cls, value: object) -> object:
        if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
            raise ValueError("canonical_url 必须是规范 URL 字符串")
        if any(character == "\\" or _is_control(character) for character in value):
            raise ValueError("canonical_url 包含歧义字符")
        try:
            parsed = urlsplit(value)
            host = normalized_http_hostname(value)
            port = parsed.port
        except (UnicodeError, ValueError):
            raise ValueError("canonical_url 格式无效") from None
        canonical_netloc = host if port is None else f"{host}:{port}"
        canonical = urlunsplit((parsed.scheme, canonical_netloc, parsed.path or "/", parsed.query, ""))
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.scheme != parsed.scheme.lower()
            or parsed.hostname != host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.scheme == "http" and port == 80)
            or (parsed.scheme == "https" and port == 443)
            or canonical != value
        ):
            raise ValueError("canonical_url 尚未规范化")
        return value

    @field_validator("published_at", "crawled_at")
    @classmethod
    def normalize_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间值必须包含时区信息")
        return value.astimezone(UTC)


class PolicyQuery(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    keyword: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    crawled_from: datetime | None = None
    crawled_to: datetime | None = None
    publisher: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    category_id: PositiveId | None = None
    source_id: PositiveId | None = None
    page: PositiveId = 1
    page_size: Annotated[int, Field(strict=True, ge=1)] = 50
    sort_by: Literal["published_at", "last_crawled_at"] = "published_at"
    sort_order: Literal["asc", "desc"] = "desc"

    @field_validator("keyword", "publisher")
    @classmethod
    def reject_untrimmed_query_text(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(_is_control(character) for character in value)
        ):
            raise ValueError("查询文本必须为规范文本")
        return value

    @field_validator("published_from", "published_to", "crawled_from", "crawled_to")
    @classmethod
    def normalize_query_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间筛选必须包含时区信息")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_ranges(self) -> PolicyQuery:
        if self.published_from and self.published_to and self.published_from > self.published_to:
            raise ValueError("发布时间范围无效")
        if self.crawled_from and self.crawled_to and self.crawled_from > self.crawled_to:
            raise ValueError("抓取时间范围无效")
        return self


class PolicyReferenceRead(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: int
    code: str
    name: str


class PolicyListItem(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: int
    title: str
    canonical_url: str
    publisher: str
    category: PolicyReferenceRead
    source: PolicyReferenceRead
    published_at: datetime
    first_crawled_at: datetime
    last_crawled_at: datetime
    content_hash: str
    latest_task_id: int | None


class PolicyDetail(PolicyListItem):
    content_text: str


class PolicyPage(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[PolicyListItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    sort_by: Literal["published_at", "last_crawled_at"]
    sort_order: Literal["asc", "desc"]


class PolicyUpsertResult(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    policy_id: int = Field(ge=1)
    outcome: Literal["stored", "duplicate", "updated"]


def _is_control(character: str) -> bool:
    codepoint = ord(character)
    return codepoint < 0x20 or codepoint == 0x7F
