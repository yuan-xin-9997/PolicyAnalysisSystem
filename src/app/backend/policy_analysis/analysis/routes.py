"""Policy analysis API routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request

from policy_analysis.analysis.schemas import (
    MAX_ANALYSIS_PAGE,
    MAX_ANALYSIS_PAGE_SIZE,
    MAX_TOP_WORDS,
    AnalysisTaskLogPage,
    AnalysisTaskPage,
    AnalysisTaskSummary,
    CreateAnalysisTaskRequest,
    CreateAnalysisTaskResponse,
    CreateComparisonTaskRequest,
    PolicyComparisonReport,
    WordFrequencyResult,
    WordRelationResult,
)
from policy_analysis.analysis.service import AnalysisService
from policy_analysis.auth.permissions import PageCode, require_page, require_page_csrf
from policy_analysis.auth.service import PublicUser
from policy_analysis.core.errors import APIError

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])

PositiveIdPath = Annotated[int, Path(ge=1)]
require_analysis_page = require_page(PageCode.ANALYSIS)
require_analysis_page_csrf = require_page_csrf(PageCode.ANALYSIS)


def get_analysis_service(request: Request) -> AnalysisService:
    sessions = request.app.state.database_sessions
    if sessions is None:
        raise APIError(status_code=503, code="DATABASE_UNAVAILABLE", message="数据库暂时不可用。")
    return AnalysisService(sessions)


def _validation_error() -> APIError:
    return APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")


@router.post("/tasks", response_model=CreateAnalysisTaskResponse)
def create_task(
    request: Request,
    body: CreateAnalysisTaskRequest,
    user: PublicUser = Depends(require_analysis_page_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> CreateAnalysisTaskResponse:
    max_policies = request.app.state.settings.analysis.max_policies_per_task
    if len(body.policy_ids) > max_policies:
        raise APIError(
            status_code=400,
            code="ANALYSIS_TOO_MANY_POLICIES",
            message="单次分析政策数量超出上限。",
        )
    response = service.create_task(body.policy_ids, requested_by=user.id)
    worker = getattr(request.app.state, "analysis_worker", None)
    if worker is not None and worker.can_run_tasks:
        worker.submit_next()
    return response


@router.post("/comparison-tasks", response_model=CreateAnalysisTaskResponse)
def create_comparison_task(
    request: Request,
    body: CreateComparisonTaskRequest,
    user: PublicUser = Depends(require_analysis_page_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> CreateAnalysisTaskResponse:
    unique_count = len(set(body.policy_ids))
    if unique_count < 2:
        raise APIError(
            status_code=400,
            code="COMPARISON_REQUIRES_TWO_POLICIES",
            message="政策比对至少需要选择两篇不同的政策。",
        )
    if unique_count > request.app.state.settings.analysis.max_policies_per_task:
        raise APIError(
            status_code=400,
            code="ANALYSIS_TOO_MANY_POLICIES",
            message="单次分析政策数量超出上限。",
        )
    response = service.create_task(
        body.policy_ids, requested_by=user.id, task_type="policy_comparison"
    )
    worker = getattr(request.app.state, "analysis_worker", None)
    if worker is not None and worker.can_run_tasks:
        worker.submit_next()
    return response


@router.get("/tasks", response_model=AnalysisTaskPage)
def list_tasks(
    request: Request,
    page: Annotated[int, Query(ge=1, le=MAX_ANALYSIS_PAGE)] = 1,
    page_size: Annotated[int | None, Query(ge=1, le=MAX_ANALYSIS_PAGE_SIZE)] = None,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisTaskPage:
    pagination = request.app.state.settings.pagination
    effective = page_size or pagination.default_page_size
    if effective > pagination.max_page_size:
        raise _validation_error()
    return service.list_tasks(page=page, page_size=effective)


@router.get("/tasks/{task_id}", response_model=AnalysisTaskSummary)
def get_task(
    task_id: PositiveIdPath,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisTaskSummary:
    return service.get_task(task_id)


@router.get("/tasks/{task_id}/words", response_model=WordFrequencyResult)
def list_words(
    task_id: PositiveIdPath,
    top: Annotated[int, Query(ge=1, le=MAX_TOP_WORDS)] = 50,
    sort_by: Literal["frequency", "tfidf"] = "frequency",
    policy_id: Annotated[int | None, Query(ge=1)] = None,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> WordFrequencyResult:
    return service.list_words(task_id, top=top, sort_by=sort_by, policy_id=policy_id)


@router.get("/tasks/{task_id}/relations", response_model=WordRelationResult)
def list_relations(
    task_id: PositiveIdPath,
    top: Annotated[int, Query(ge=1, le=MAX_TOP_WORDS)] = 50,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> WordRelationResult:
    return service.list_relations(task_id, top=top)


@router.get("/tasks/{task_id}/comparison-report", response_model=PolicyComparisonReport)
def get_comparison_report(
    task_id: PositiveIdPath,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> PolicyComparisonReport:
    return service.get_comparison_report(task_id)


@router.get("/tasks/{task_id}/logs", response_model=AnalysisTaskLogPage)
def list_logs(
    task_id: PositiveIdPath,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_ANALYSIS_PAGE_SIZE)] = 50,
    _user: PublicUser = Depends(require_analysis_page),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisTaskLogPage:
    return service.list_logs(task_id, page=page, page_size=page_size)
