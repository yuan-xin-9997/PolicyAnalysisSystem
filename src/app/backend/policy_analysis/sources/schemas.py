"""Validated API and service boundary models for collection sources."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from policy_analysis.sources.url_validation import normalized_http_hostname

TrimmedCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
TrimmedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
HttpUrlText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _validate_http_url(value: str) -> str:
    try:
        normalized_http_hostname(value)
    except ValueError:
        raise ValueError("URL 格式无效") from None
    return value


ValidatedHttpUrlText = Annotated[HttpUrlText, AfterValidator(_validate_http_url)]


class DiscoveryConfig(StrictModel):
    rss_urls: list[ValidatedHttpUrlText] = Field(default_factory=list, max_length=32)
    channel_urls: list[ValidatedHttpUrlText] = Field(default_factory=list, max_length=32)
    channel_fetch_mode: Literal["auto", "http", "browser"] = "auto"

    @field_validator("rss_urls", "channel_urls")
    @classmethod
    def deduplicate_urls(cls, values: list[str]) -> list[str]:
        return _deduplicate(values)

    @model_validator(mode="after")
    def require_an_entry(self) -> DiscoveryConfig:
        if not self.rss_urls and not self.channel_urls:
            raise ValueError("至少配置一个 RSS 或栏目入口")
        if len(self.rss_urls) + len(self.channel_urls) > 48:
            raise ValueError("发现入口数量超过上限")
        return self


class CollectionRuleCreate(StrictModel):
    name: TrimmedName
    source_code: TrimmedCode
    category_code: TrimmedCode
    include_keywords: list[Keyword] = Field(min_length=1, max_length=64)
    exclude_keywords: list[Keyword] = Field(default_factory=list, max_length=64)
    history_years: int = Field(default=5, ge=1, le=20)
    discovery: DiscoveryConfig
    is_active: bool = True
    trigger_mode: Literal["manual", "schedule"] = "manual"
    cron_expression: str | None = Field(default=None, max_length=128)
    schedule_enabled: bool = False

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def deduplicate_keywords(cls, values: list[str]) -> list[str]:
        return _deduplicate(values)


class CollectionRuleUpdate(StrictModel):
    name: TrimmedName | None = None
    source_code: TrimmedCode | None = None
    category_code: TrimmedCode | None = None
    include_keywords: list[Keyword] | None = Field(default=None, min_length=1, max_length=64)
    exclude_keywords: list[Keyword] | None = Field(default=None, max_length=64)
    history_years: int | None = Field(default=None, ge=1, le=20)
    discovery: DiscoveryConfig | None = None
    is_active: bool | None = None
    trigger_mode: Literal["manual", "schedule"] | None = None
    cron_expression: str | None = Field(default=None, max_length=128)
    schedule_enabled: bool | None = None

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def deduplicate_keywords(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _deduplicate(values)

    @field_validator("cron_expression")
    @classmethod
    def strip_cron(cls, value: str | None) -> str | None:
        return None if value is None else " ".join(value.split())

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> CollectionRuleUpdate:
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("PATCH 字段不得为 null")
        return self


class PolicyCategoryRead(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    code: str
    name: str
    description: str | None
    is_active: bool


class SourceRead(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    code: str
    name: str
    organization: str
    base_url: str
    adapter_type: str
    allowed_domains: list[str]
    is_active: bool


class CollectionRuleRead(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    name: str
    source: SourceRead
    category: PolicyCategoryRead
    include_keywords: list[str]
    exclude_keywords: list[str]
    history_years: int
    discovery: DiscoveryConfig
    is_active: bool
    trigger_mode: Literal["manual", "schedule"]
    cron_expression: str | None
    schedule_timezone: str
    schedule_enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SeedUrlImport(StrictModel):
    url: ValidatedHttpUrlText
    expected_title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    expected_published_date: date
    is_verified: bool = False


class SeedImportResult(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inserted: int = Field(ge=0)
    existing: int = Field(ge=0)
