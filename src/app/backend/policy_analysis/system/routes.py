"""System metadata and lightweight health endpoints."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from policy_analysis.auth.dependencies import require_user
from policy_analysis.auth.service import PublicUser

router = APIRouter(prefix="/api/v1", tags=["system"])
health_router = APIRouter(prefix="/health", tags=["health"])
BEIJING = ZoneInfo("Asia/Shanghai")


@router.get("/system/info")
def system_info(
    request: Request,
    _user: PublicUser = Depends(require_user),
) -> dict[str, object]:
    version, commit_sha = resolve_build_metadata(
        request.app.state.version_environment,
        request.app.state.project_root,
    )
    database_status = _database_status(request)
    return {
        "version": version,
        "commit_sha": commit_sha,
        "server_time": datetime.now(BEIJING).isoformat(),
        "timezone": "Asia/Shanghai",
        "health": {
            "live": "ok",
            "database": database_status,
            "task_executor": "not_configured",
        },
    }


@health_router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
def ready(request: Request) -> JSONResponse:
    database_status = _database_status(request)
    ready_status = database_status == "ok"
    return JSONResponse(
        status_code=200 if ready_status else 503,
        content={
            "status": "ready" if ready_status else "not_ready",
            "checks": {
                "database": {"status": database_status},
                "task_executor": {"status": "not_configured"},
            },
        },
    )


def resolve_build_metadata(environment: Mapping[str, str], project_root: Path) -> tuple[str, str]:
    version = environment.get("POLICY_ANALYSIS_VERSION", "").strip()
    commit_sha = environment.get("POLICY_ANALYSIS_COMMIT_SHA", "").strip()
    if version and commit_sha:
        return version, commit_sha[:7]
    try:
        count = _git_output(project_root, "rev-list", "--count", "HEAD")
        git_sha = _git_output(project_root, "rev-parse", "--short=7", "HEAD")
    except (OSError, subprocess.SubprocessError):
        count, git_sha = "", ""
    resolved_version = version or (f"v0.{count}" if count.isdigit() else "v0.dev")
    resolved_sha = (commit_sha or git_sha or "unknown")[:7]
    return resolved_version, resolved_sha


def _git_output(project_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )
    return result.stdout.strip()


def _database_status(request: Request) -> str:
    try:
        with request.app.state.database_sessions() as database:
            database.execute(text("SELECT 1")).scalar_one()
    except Exception:
        return "error"
    return "ok"
