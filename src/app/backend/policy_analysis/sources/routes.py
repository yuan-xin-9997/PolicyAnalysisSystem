"""Source, collection-rule, and schedule HTTP routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from policy_analysis.auth.permissions import PageCode, require_admin_csrf, require_page
from policy_analysis.auth.service import PublicUser
from policy_analysis.core.errors import APIError
from policy_analysis.sources.schemas import (
    CollectionRuleCreate,
    CollectionRuleRead,
    CollectionRuleUpdate,
    PolicyCategoryRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    SourceRead,
)
from policy_analysis.sources.service import SourceService

router = APIRouter(prefix="/api/v1", tags=["sources"])
PositiveId = Annotated[int, Path(ge=1)]
require_tasks_page = require_page(PageCode.TASKS)


def get_source_service(request: Request) -> SourceService:
    sessions = request.app.state.database_sessions
    if sessions is None:
        raise APIError(status_code=503, code="DATABASE_UNAVAILABLE", message="数据库暂时不可用。")
    return SourceService(sessions)


def _reject_query_parameters(request: Request) -> None:
    if request.query_params:
        raise APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")


@router.get("/policy-categories", response_model=list[PolicyCategoryRead])
def list_policy_categories(
    request: Request,
    _user: PublicUser = Depends(require_tasks_page),
    service: SourceService = Depends(get_source_service),
) -> list[PolicyCategoryRead]:
    _reject_query_parameters(request)
    return service.list_categories()


@router.get("/sources", response_model=list[SourceRead])
def list_sources(
    request: Request,
    _user: PublicUser = Depends(require_tasks_page),
    service: SourceService = Depends(get_source_service),
) -> list[SourceRead]:
    _reject_query_parameters(request)
    return service.list_sources()


@router.get("/collection-rules", response_model=list[CollectionRuleRead])
def list_collection_rules(
    request: Request,
    _user: PublicUser = Depends(require_tasks_page),
    service: SourceService = Depends(get_source_service),
) -> list[CollectionRuleRead]:
    _reject_query_parameters(request)
    return service.list_rules()


@router.post(
    "/collection-rules",
    response_model=CollectionRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_collection_rule(
    payload: CollectionRuleCreate,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: SourceService = Depends(get_source_service),
) -> CollectionRuleRead:
    return service.create_rule(payload)


@router.patch("/collection-rules/{rule_id}", response_model=CollectionRuleRead)
def update_collection_rule(
    rule_id: PositiveId,
    payload: CollectionRuleUpdate,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: SourceService = Depends(get_source_service),
) -> CollectionRuleRead:
    return service.update_rule(rule_id, payload)


@router.get("/schedules", response_model=list[ScheduleRead])
def list_schedules(
    request: Request,
    _user: PublicUser = Depends(require_tasks_page),
    service: SourceService = Depends(get_source_service),
) -> list[ScheduleRead]:
    _reject_query_parameters(request)
    return service.list_schedules()


@router.post(
    "/schedules",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    payload: ScheduleCreate,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: SourceService = Depends(get_source_service),
) -> ScheduleRead:
    return service.create_schedule(payload)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: PositiveId,
    payload: ScheduleUpdate,
    _admin: PublicUser = Depends(require_admin_csrf),
    service: SourceService = Depends(get_source_service),
) -> ScheduleRead:
    return service.update_schedule(schedule_id, payload)
