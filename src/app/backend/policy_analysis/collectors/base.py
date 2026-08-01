"""Stable public types shared by collection adapters and task runners."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    request_id: str
    artifact_id: str
    title: str
    content: str
    author: str
    published_hint: str


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    url: str
    text: str
    origin: str


class WebFetchClientError(Exception):
    """A stable, sanitized WebFetch failure for downstream task handling."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
