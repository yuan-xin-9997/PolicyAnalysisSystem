"""Read-only policy search and detail API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import ValidationError

from policy_analysis.auth.permissions import PageCode, require_page
from policy_analysis.auth.service import PublicUser
from policy_analysis.core.errors import APIError
from policy_analysis.policies.schemas import PolicyDetail, PolicyPage, PolicyQuery
from policy_analysis.policies.service import PolicyService

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])
PositiveIdPath = Annotated[int, Path(ge=1)]
require_policies_page = require_page(PageCode.POLICIES)
_LIST_QUERY_PARAMETERS = {
    "keyword",
    "published_from",
    "published_to",
    "crawled_from",
    "crawled_to",
    "publisher",
    "category_id",
    "source_id",
    "page",
    "page_size",
    "sort_by",
    "sort_order",
}


def get_policy_service(request: Request) -> PolicyService:
    sessions = request.app.state.database_sessions
    if sessions is None:
        raise APIError(status_code=503, code="DATABASE_UNAVAILABLE", message="数据库暂时不可用。")
    return PolicyService(sessions)


@router.get("", response_model=PolicyPage)
def list_policies(
    request: Request,
    keyword: Annotated[str | None, Query(max_length=512)] = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    crawled_from: datetime | None = None,
    crawled_to: datetime | None = None,
    publisher: Annotated[str | None, Query(max_length=256)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    source_id: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int | None, Query(ge=1)] = None,
    sort_by: Literal["published_at", "last_crawled_at"] = "published_at",
    sort_order: Literal["asc", "desc"] = "desc",
    _user: PublicUser = Depends(require_policies_page),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyPage:
    _validate_query_keys(request, _LIST_QUERY_PARAMETERS)
    pagination = request.app.state.settings.pagination
    effective_page_size = page_size or pagination.default_page_size
    if effective_page_size > pagination.max_page_size:
        raise _validation_error()
    try:
        query = PolicyQuery(
            keyword=keyword,
            published_from=published_from,
            published_to=published_to,
            crawled_from=crawled_from,
            crawled_to=crawled_to,
            publisher=publisher,
            category_id=category_id,
            source_id=source_id,
            page=page,
            page_size=effective_page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValidationError:
        raise _validation_error() from None
    return service.search(query)


@router.get("/{policy_id}", response_model=PolicyDetail)
def get_policy(
    policy_id: PositiveIdPath,
    request: Request,
    _user: PublicUser = Depends(require_policies_page),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyDetail:
    _validate_query_keys(request, set())
    return service.detail(policy_id)


def _validate_query_keys(request: Request, allowed: set[str]) -> None:
    if set(request.query_params) - allowed or any(
        len(request.query_params.getlist(name)) != 1 for name in request.query_params
    ):
        raise _validation_error()


def _validation_error() -> APIError:
    return APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")
