from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskSeedSnapshot(_Strict):
    url: str = Field(min_length=1, max_length=2048)
    expected_title: str = Field(min_length=1, max_length=512)
    expected_published_date: date
    is_verified: bool


class TaskSourceSnapshot(_Strict):
    id: int = Field(ge=1)
    is_active: bool
    adapter_type: str = Field(min_length=1, max_length=128)
    allowed_domains: list[str] = Field(min_length=1, max_length=64)


class TaskCategorySnapshot(_Strict):
    id: int = Field(ge=1)
    is_active: bool


class TaskRuleSnapshot(_Strict):
    id: int = Field(ge=1)
    is_active: bool
    source: TaskSourceSnapshot
    category: TaskCategorySnapshot
    history_years: int = Field(ge=1, le=20)
    include_keywords: list[str] = Field(min_length=1, max_length=64)
    exclude_keywords: list[str] = Field(max_length=64)
    discovery: dict[str, Any]
    seeds: list[TaskSeedSnapshot] = Field(max_length=10_000)


class TaskRequestSnapshot(_Strict):
    version: Literal[1]
    request: dict[str, Any]
    rule: TaskRuleSnapshot


__all__ = ["TaskRequestSnapshot"]
