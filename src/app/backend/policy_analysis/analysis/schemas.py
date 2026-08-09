"""Request and response models for the policy analysis API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_POLICY_IDS = 100
MAX_TOP_WORDS = 500
MAX_ANALYSIS_PAGE = 1_000_000
MAX_ANALYSIS_PAGE_SIZE = 100


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateAnalysisTaskRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    policy_ids: list[Annotated[int, Field(strict=True, ge=1)]] = Field(
        min_length=1, max_length=MAX_POLICY_IDS
    )


class CreateAnalysisTaskResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    task_id: int = Field(ge=1)
    status: str


class AnalysisTaskSummary(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: int
    task_type: str
    status: str
    policy_count: int = Field(ge=0)
    requested_by: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None
    created_at: datetime


class AnalysisTaskPage(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[AnalysisTaskSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=MAX_ANALYSIS_PAGE)
    page_size: int = Field(ge=1, le=MAX_ANALYSIS_PAGE_SIZE)


class WordFrequencyItem(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    word: str
    frequency: int = Field(ge=0)
    tfidf: float = Field(ge=0)
    doc_count: int = Field(ge=0)


class WordFrequencyResult(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[WordFrequencyItem]
    total: int = Field(ge=0)


class WordRelationItem(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    word1: str
    word2: str
    co_count: int = Field(ge=0)


class WordRelationResult(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[WordRelationItem]
    nodes: list[str]


class AnalysisTaskLogItem(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: int
    level: str
    message: str
    context: dict[str, object]
    created_at: datetime


class AnalysisTaskLogPage(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[AnalysisTaskLogItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


BoundedWord = Annotated[str, StringConstraints(min_length=1, max_length=128)]
