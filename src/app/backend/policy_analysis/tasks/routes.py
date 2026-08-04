from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from policy_analysis.auth.permissions import PageCode, require_admin_csrf, require_page
from policy_analysis.auth.service import PublicUser
from policy_analysis.core.errors import APIError
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem, CrawlTaskLog
from policy_analysis.tasks.repository import TaskRepository, TaskRepositoryError

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
PositiveId = Annotated[int, Path(ge=1)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
require_tasks_page = require_page(PageCode.TASKS)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: int = Field(ge=1)


def get_task_repository(request: Request) -> TaskRepository:
    sessions = request.app.state.database_sessions
    if sessions is None:
        raise APIError(status_code=503, code="DATABASE_UNAVAILABLE", message="数据库暂时不可用。")
    settings = getattr(request.app.state, "settings", None)
    secrets = []
    if settings is not None:
        secrets.append(settings.webfetch.api_key.get_secret_value())
    return TaskRepository(sessions, secrets=secrets)


@router.get("")
def list_tasks(
    _user: PublicUser = Depends(require_tasks_page),
    repository: TaskRepository = Depends(get_task_repository),
    page: Page = 1,
    page_size: PageSize = 50,
) -> dict[str, object]:
    tasks, total = repository.list_tasks(offset=(page - 1) * page_size, limit=page_size)
    return _page([_task_to_read(task) for task in tasks], total, page, page_size)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    request: Request,
    payload: TaskCreate,
    admin: PublicUser = Depends(require_admin_csrf),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict[str, object]:
    try:
        task = repository.create_task(
            payload.rule_id,
            "manual",
            {"kind": "manual"},
            datetime.now(UTC),
            requested_by=admin.id,
        )
    except TaskRepositoryError as error:
        raise _api_error(error) from None
    _wake_worker(request)
    return _task_to_read(task)


@router.get("/{task_id}")
def get_task(
    task_id: PositiveId,
    _user: PublicUser = Depends(require_tasks_page),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict[str, object]:
    task = repository.get(task_id)
    if task is None:
        raise APIError(status_code=404, code="TASK_NOT_FOUND", message="采集任务不存在。")
    return _task_to_read(task)


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: PositiveId,
    _admin: PublicUser = Depends(require_admin_csrf),
    repository: TaskRepository = Depends(get_task_repository),
) -> dict[str, object]:
    try:
        repository.request_cancel(task_id, datetime.now(UTC))
    except TaskRepositoryError as error:
        raise _api_error(error) from None
    task = repository.get(task_id)
    if task is None:
        raise APIError(status_code=404, code="TASK_NOT_FOUND", message="采集任务不存在。")
    return _task_to_read(task)


@router.get("/{task_id}/logs")
def list_task_logs(
    task_id: PositiveId,
    _user: PublicUser = Depends(require_tasks_page),
    repository: TaskRepository = Depends(get_task_repository),
    page: Page = 1,
    page_size: PageSize = 50,
) -> dict[str, object]:
    _require_task(repository, task_id)
    logs, total = repository.list_logs(task_id, offset=(page - 1) * page_size, limit=page_size)
    return _page([_log_to_read(log) for log in logs], total, page, page_size)


@router.get("/{task_id}/items")
def list_task_items(
    task_id: PositiveId,
    _user: PublicUser = Depends(require_tasks_page),
    repository: TaskRepository = Depends(get_task_repository),
    page: Page = 1,
    page_size: PageSize = 50,
) -> dict[str, object]:
    _require_task(repository, task_id)
    items, total = repository.list_items(task_id, offset=(page - 1) * page_size, limit=page_size)
    return _page([_item_to_read(item) for item in items], total, page, page_size)


def _require_task(repository: TaskRepository, task_id: int) -> CrawlTask:
    task = repository.get(task_id)
    if task is None:
        raise APIError(status_code=404, code="TASK_NOT_FOUND", message="采集任务不存在。")
    return task


def _task_to_read(task: CrawlTask) -> dict[str, object]:
    processed = task.success_count + task.duplicate_count + task.filtered_count + task.failed_count
    return {
        "id": task.id,
        "rule_id": task.rule_id,
        "trigger_type": task.trigger_type,
        "status": task.status,
        "requested_by": task.requested_by,
        "scheduled_for": task.scheduled_for,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "cancel_requested_at": task.cancel_requested_at,
        "error_summary": task.error_summary,
        "progress": {"processed": processed, "discovered": task.discovered_count},
        "counts": {
            "success": task.success_count,
            "duplicate": task.duplicate_count,
            "filtered": task.filtered_count,
            "failed": task.failed_count,
            "total_terminal_items": processed,
        },
    }


def _log_to_read(log: CrawlTaskLog) -> dict[str, object]:
    try:
        context: Any = json.loads(log.context_json)
    except json.JSONDecodeError:
        context = {}
    return {
        "id": log.id,
        "level": log.level,
        "message": log.message,
        "context": context,
        "created_at": log.created_at,
    }


def _item_to_read(item: CrawlTaskItem) -> dict[str, object]:
    return {
        "id": item.id,
        "candidate_url": item.candidate_url,
        "normalized_url": item.normalized_url,
        "status": item.status,
        "policy_id": item.policy_id,
        "attempt_count": item.attempt_count,
        "reason_code": item.reason_code,
        "reason_message": item.reason_message,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
    }


def _page(items: list[dict[str, object]], total: int, page: int, page_size: int) -> dict[str, object]:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _api_error(error: TaskRepositoryError) -> APIError:
    status_code = 404 if error.code.endswith("NOT_FOUND") else 422
    return APIError(status_code=status_code, code=error.code, message=str(error))


def _wake_worker(request: Request) -> None:
    worker = getattr(request.app.state, "task_worker", None)
    if worker is not None and worker.is_started:
        worker.submit_next()


__all__ = ["router"]
