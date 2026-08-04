from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import PurePath
from threading import RLock
from typing import Any
from urllib.parse import unquote

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from policy_analysis.sources.models import CollectionRule, Schedule, SeedUrl
from policy_analysis.tasks.models import CrawlTask, CrawlTaskItem, CrawlTaskLog, TaskItemStatus, TaskStatus
from policy_analysis.tasks.schemas import TaskRequestSnapshot
from policy_analysis.tasks.state import transition

_SENSITIVE_KEYS = frozenset(
    {
        "auth",
        "authorization",
        "apikey",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "cookie",
        "setcookie",
        "credential",
        "session",
        "sessionid",
        "privatekey",
        "accesskey",
        "path",
    }
)
_QUERY_PARAMETER = re.compile(r"([?&])([^=&#]+)=([^&#]*)")
_WINDOWS_PATH = re.compile(r"(?i)(?:[A-Z]:\\|\\\\)[^\r\n]+")
_WEB_URL = re.compile(r"(?i)(?:https?:)?//[^\s\"'<>]+")
_LOCAL_PATH = re.compile(r"(?m)(^|[=\s(:'\"])(/[^\r\n,;)'\"]*)")
_MAX_DEPTH = 12
_MAX_ITEMS = 100
_MAX_STRING = 4096
_SCHEDULE_LOCK = RLock()


class TaskRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskRepository:
    def __init__(self, sessions: sessionmaker[Session], *, secrets: Iterable[str] = ()) -> None:
        self._sessions = sessions
        self._secrets = tuple(sorted(set(filter(None, secrets)), key=len, reverse=True))

    def get(self, task_id: int) -> CrawlTask | None:
        with self._sessions() as session:
            return session.get(CrawlTask, task_id)

    def list_tasks(self, *, offset: int = 0, limit: int = 50) -> tuple[list[CrawlTask], int]:
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(CrawlTask)) or 0
            tasks = list(
                session.scalars(select(CrawlTask).order_by(CrawlTask.id.desc()).offset(offset).limit(limit))
            )
            session.expunge_all()
            return tasks, total

    def create_task(
        self,
        rule_id: int,
        trigger_type: str,
        request_snapshot: Mapping[str, Any],
        now: datetime,
        *,
        requested_by: int | None = None,
        scheduled_for: datetime | None = None,
    ) -> CrawlTask:
        _utc(now)
        if trigger_type not in {"manual", "schedule"}:
            raise TaskRepositoryError("TASK_TRIGGER_INVALID", "任务触发类型无效。")
        if _invalid_snapshot(request_snapshot, self._secrets):
            raise TaskRepositoryError("TASK_SNAPSHOT_INVALID", "任务请求快照无效。") from None
        with self._sessions.begin() as session:
            rule = session.get(CollectionRule, rule_id)
            if rule is None:
                raise TaskRepositoryError("RULE_NOT_FOUND", "采集规则不存在。")
            _ = rule.source, rule.category
            seeds = list(
                session.scalars(select(SeedUrl).where(SeedUrl.rule_id == rule.id).order_by(SeedUrl.id))
            )
            try:
                snapshot_model = TaskRequestSnapshot.model_validate(
                    {
                        "version": 1,
                        "request": dict(request_snapshot),
                        "rule": {
                            "id": rule.id,
                            "is_active": rule.is_active,
                            "source": {
                                "id": rule.source.id,
                                "is_active": rule.source.is_active,
                                "adapter_type": rule.source.adapter_type,
                                "allowed_domains": json.loads(rule.source.allowed_domains_json),
                            },
                            "category": {"id": rule.category.id, "is_active": rule.category.is_active},
                            "history_years": rule.history_years,
                            "include_keywords": json.loads(rule.include_keywords_json),
                            "exclude_keywords": json.loads(rule.exclude_keywords_json),
                            "discovery": json.loads(rule.discovery_config_json),
                            "seeds": [
                                {
                                    "url": seed.url,
                                    "expected_title": seed.expected_title,
                                    "expected_published_date": seed.expected_published_date,
                                    "is_verified": seed.is_verified,
                                }
                                for seed in seeds
                            ],
                        },
                    }
                )
                payload = snapshot_model.model_dump(mode="json")
                if _invalid_snapshot(payload, self._secrets):
                    raise ValueError("unsafe snapshot")
                snapshot = snapshot_model.model_dump_json()
            except (TypeError, ValueError, json.JSONDecodeError):
                raise TaskRepositoryError("TASK_SNAPSHOT_INVALID", "任务请求快照无效。") from None
            task = CrawlTask(
                rule_id=rule_id,
                trigger_type=trigger_type,
                status=TaskStatus.PENDING.value,
                requested_by=requested_by,
                scheduled_for=None if scheduled_for is None else _utc(scheduled_for),
                request_snapshot_json=snapshot,
            )
            session.add(task)
            session.flush()
            task_id = task.id
        stored = self.get(task_id)
        if stored is None:  # pragma: no cover - defensive database invariant
            raise TaskRepositoryError("TASK_CREATE_FAILED", "采集任务创建失败。")
        return stored

    def create_scheduled_task_once(
        self,
        schedule_id: int,
        scheduled_for: datetime,
        now: datetime,
    ) -> CrawlTask | None:
        scheduled_for = _utc(scheduled_for)
        with _SCHEDULE_LOCK:
            with self._sessions() as session:
                schedule = session.get(Schedule, schedule_id)
                if schedule is None or not schedule.is_active:
                    return None
                existing = session.scalar(
                    select(CrawlTask.id).where(
                        CrawlTask.trigger_type == "schedule",
                        CrawlTask.rule_id == schedule.rule_id,
                        CrawlTask.scheduled_for == scheduled_for,
                    )
                )
                rule_id = schedule.rule_id
            if existing is not None:
                return None
            created = self.create_task(
                rule_id,
                "schedule",
                {"kind": "schedule"},
                now,
                scheduled_for=scheduled_for,
            )
            with self._sessions.begin() as session:
                schedule = session.get(Schedule, schedule_id)
                if schedule is not None:
                    schedule.last_run_at = scheduled_for
            return created

    def claim_next(self, now: datetime) -> int | None:
        now = _utc(now)
        with self._sessions.begin() as session:
            running = aliased(CrawlTask)
            candidate = session.scalar(
                select(CrawlTask)
                .where(
                    CrawlTask.status == TaskStatus.PENDING.value,
                    ~select(running.id)
                    .where(
                        running.status == TaskStatus.RUNNING.value,
                        running.rule_id == CrawlTask.rule_id,
                    )
                    .exists(),
                )
                .order_by(CrawlTask.id)
            )
            if candidate is None:
                return None
            claimed = session.execute(
                update(CrawlTask)
                .where(
                    CrawlTask.id == candidate.id,
                    CrawlTask.status == TaskStatus.PENDING.value,
                    ~select(running.id)
                    .where(
                        running.status == TaskStatus.RUNNING.value,
                        running.rule_id == candidate.rule_id,
                    )
                    .exists(),
                )
                .values(status=TaskStatus.RUNNING.value, started_at=now)
            ).rowcount
            return candidate.id if claimed == 1 else None

    def recover_interrupted(self, now: datetime) -> list[int]:
        now = _utc(now)
        with self._sessions.begin() as session:
            task_ids = list(
                session.scalars(
                    select(CrawlTask.id)
                    .where(CrawlTask.status == TaskStatus.RUNNING.value)
                    .order_by(CrawlTask.id)
                )
            )
            if not task_ids:
                return []
            session.execute(
                update(CrawlTask)
                .where(CrawlTask.id.in_(task_ids))
                .values(
                    status=TaskStatus.FAILED.value,
                    finished_at=now,
                    error_summary="服务异常中断",
                )
            )
            return task_ids

    def claim(self, task_id: int, now: datetime) -> TaskStatus:
        with self._sessions.begin() as session:
            task = session.get(CrawlTask, task_id)
            if task is None:
                raise TaskRepositoryError("TASK_NOT_FOUND", "采集任务不存在。")
            try:
                current = TaskStatus(task.status)
            except ValueError:
                raise TaskRepositoryError("TASK_STATE_INVALID", "采集任务状态无效。") from None
            if current is not TaskStatus.PENDING:
                if current is TaskStatus.RUNNING:
                    raise TaskRepositoryError("TASK_ALREADY_CLAIMED", "采集任务已被领取。")
                return current
            if task.cancel_requested_at is not None:
                transition(current, TaskStatus.CANCELLED)
                task.status = TaskStatus.CANCELLED.value
                task.finished_at = _utc(now)
                return TaskStatus.CANCELLED
            statement = (
                update(CrawlTask)
                .where(CrawlTask.id == task_id, CrawlTask.status == TaskStatus.PENDING.value)
                .values(status=TaskStatus.RUNNING.value, started_at=_utc(now))
            )
            if session.execute(statement).rowcount != 1:
                raise TaskRepositoryError("TASK_ALREADY_CLAIMED", "采集任务已被领取。")
            return TaskStatus.RUNNING

    def load_context(self, task_id: int) -> tuple[CrawlTask, CollectionRule, list[SeedUrl]]:
        with self._sessions() as session:
            task = session.get(CrawlTask, task_id)
            if task is None:
                raise TaskRepositoryError("TASK_NOT_FOUND", "采集任务不存在。")
            rule = session.get(CollectionRule, task.rule_id)
            if rule is None:
                raise TaskRepositoryError("RULE_NOT_FOUND", "采集规则不存在。")
            _ = rule.source, rule.category
            seeds = list(
                session.scalars(select(SeedUrl).where(SeedUrl.rule_id == rule.id).order_by(SeedUrl.id))
            )
            session.expunge_all()
            return task, rule, seeds

    def is_cancel_requested(self, task_id: int) -> bool:
        with self._sessions() as session:
            value = session.scalar(select(CrawlTask.cancel_requested_at).where(CrawlTask.id == task_id))
            return value is not None

    def set_discovered_count(self, task_id: int, count: int) -> None:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise TaskRepositoryError("TASK_COUNT_INVALID", "任务发现数量无效。")
        with self._sessions.begin() as session:
            task = session.get(CrawlTask, task_id)
            if task is None:
                raise TaskRepositoryError("TASK_NOT_FOUND", "采集任务不存在。")
            if TaskStatus(task.status) is not TaskStatus.RUNNING or count < task.discovered_count:
                raise TaskRepositoryError("TASK_COUNT_INVALID", "任务发现数量无法更新。")
            task.discovered_count = count

    def request_cancel(self, task_id: int, now: datetime) -> None:
        with self._sessions.begin() as session:
            task = session.get(CrawlTask, task_id)
            if task is None:
                raise TaskRepositoryError("TASK_NOT_FOUND", "采集任务不存在。")
            if (
                TaskStatus(task.status) in {TaskStatus.PENDING, TaskStatus.RUNNING}
                and task.cancel_requested_at is None
            ):
                task.cancel_requested_at = _utc(now)

    def list_logs(self, task_id: int, *, offset: int = 0, limit: int = 50) -> tuple[list[CrawlTaskLog], int]:
        with self._sessions() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(CrawlTaskLog).where(CrawlTaskLog.task_id == task_id)
                )
                or 0
            )
            logs = list(
                session.scalars(
                    select(CrawlTaskLog)
                    .where(CrawlTaskLog.task_id == task_id)
                    .order_by(CrawlTaskLog.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            session.expunge_all()
            return logs, total

    def list_items(
        self, task_id: int, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[CrawlTaskItem], int]:
        with self._sessions() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(CrawlTaskItem).where(CrawlTaskItem.task_id == task_id)
                )
                or 0
            )
            items = list(
                session.scalars(
                    select(CrawlTaskItem)
                    .where(CrawlTaskItem.task_id == task_id)
                    .order_by(CrawlTaskItem.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            session.expunge_all()
            return items, total

    def due_schedules(self, now: datetime) -> list[Schedule]:
        now = _utc(now)
        with self._sessions() as session:
            schedules = list(
                session.scalars(
                    select(Schedule)
                    .where(
                        Schedule.is_active.is_(True),
                        Schedule.next_run_at.is_not(None),
                        Schedule.next_run_at <= now,
                    )
                    .order_by(Schedule.id)
                )
            )
            session.expunge_all()
            return schedules

    def enabled_schedules(self) -> list[Schedule]:
        with self._sessions() as session:
            schedules = list(
                session.scalars(select(Schedule).where(Schedule.is_active.is_(True)).order_by(Schedule.id))
            )
            session.expunge_all()
            return schedules

    def create_item(self, task_id: int, candidate_url: str, normalized_url: str, now: datetime) -> int:
        with self._sessions.begin() as session:
            item = CrawlTaskItem(
                task_id=task_id,
                candidate_url=candidate_url,
                normalized_url=normalized_url,
                status=TaskItemStatus.FAILED.value,
                attempt_count=1,
                reason_code="PROCESSING",
                reason_message="候选文章正在处理。",
                started_at=_utc(now),
            )
            session.add(item)
            session.flush()
            return item.id

    def finish_item(
        self,
        item_id: int,
        status: TaskItemStatus,
        now: datetime,
        *,
        policy_id: int | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            self.finish_item_in_session(
                session,
                item_id,
                status,
                now,
                policy_id=policy_id,
                reason_code=reason_code,
                reason_message=reason_message,
            )

    def finish_item_in_session(
        self,
        session: Session,
        item_id: int,
        status: TaskItemStatus,
        now: datetime,
        *,
        policy_id: int | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
    ) -> None:
        item = session.get(CrawlTaskItem, item_id)
        if item is None:
            raise TaskRepositoryError("TASK_ITEM_NOT_FOUND", "任务明细不存在。")
        item.status = status.value
        item.policy_id = policy_id
        item.reason_code = reason_code
        item.reason_message = _safe_text(reason_message or "", self._secrets) or None
        item.finished_at = _utc(now)
        session.flush()

    def finish(
        self, task_id: int, status: TaskStatus, now: datetime, *, error_summary: str | None = None
    ) -> TaskStatus:
        with self._sessions.begin() as session:
            task = session.get(CrawlTask, task_id)
            if task is None:
                raise TaskRepositoryError("TASK_NOT_FOUND", "采集任务不存在。")
            current = TaskStatus(task.status)
            if current is not TaskStatus.RUNNING:
                return current
            transition(current, status)
            counts = {value.value: 0 for value in TaskItemStatus}
            for value in session.scalars(
                select(CrawlTaskItem.status).where(CrawlTaskItem.task_id == task_id)
            ):
                counts[value] += 1
            if sum(counts.values()) > task.discovered_count:
                raise TaskRepositoryError("TASK_STATISTICS_INVALID", "任务统计数据无效。")
            task.success_count = counts["stored"] + counts["updated"]
            task.duplicate_count = counts["duplicate"]
            task.filtered_count = counts["filtered"]
            task.failed_count = counts["failed"]
            task.status = status.value
            task.finished_at = _utc(now)
            task.error_summary = _safe_text(error_summary or "", self._secrets) or None
            return status

    def add_log(
        self, task_id: int, level: str, message: str, context: Mapping[str, Any] | None = None
    ) -> None:
        if level not in {"debug", "info", "warning", "error"}:
            raise TaskRepositoryError("TASK_LOG_LEVEL_INVALID", "任务日志级别无效。")
        cleaned = _redact(dict(context or {}), self._secrets)
        with self._sessions.begin() as session:
            session.add(
                CrawlTaskLog(
                    task_id=task_id,
                    level=level,
                    message=_safe_text(message, self._secrets),
                    context_json=json.dumps(
                        cleaned,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )
            )


def _redact(value: Any, secrets: tuple[str, ...], key: str = "", depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "[REDACTED]"
    normalized_key = "".join(character for character in key.casefold() if character.isalnum())
    if _is_sensitive_key(normalized_key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:_MAX_ITEMS]:
            key = _safe_text(str(raw_key), secrets)[:128] or "[REDACTED]"
            base = key
            suffix = 2
            while key in cleaned:
                key = f"{base[:118]}#{suffix}"
                suffix += 1
            cleaned[key] = _redact(item, secrets, str(raw_key), depth + 1)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item, secrets, depth=depth + 1) for item in list(value)[:_MAX_ITEMS]]
    if isinstance(value, PurePath):
        return "[REDACTED]"
    if isinstance(value, float) and not math.isfinite(value):
        return "[REDACTED]"
    if isinstance(value, str):
        return _safe_text(value, secrets)
    return value if value is None or isinstance(value, (bool, int, float)) else "[REDACTED]"


def _safe_text(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        result = result.replace(secret, "[REDACTED]")
    result = _QUERY_PARAMETER.sub(_redact_query_parameter, result)
    result = _WINDOWS_PATH.sub("[REDACTED]", result)
    result = _redact_local_paths(result)
    result = "".join(
        character if character in "\n\r\t" or unicodedata.category(character) not in {"Cc", "Cf"} else " "
        for character in result
    )
    return result[:_MAX_STRING]


def _redact_local_paths(value: str) -> str:
    pieces: list[str] = []
    position = 0
    for match in _WEB_URL.finditer(value):
        pieces.append(_redact_path_segment(value[position : match.start()]))
        pieces.append(match.group(0))
        position = match.end()
    pieces.append(_redact_path_segment(value[position:]))
    return "".join(pieces)


def _redact_path_segment(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(2)
        trailing_space = path[len(path.rstrip()) :]
        return f"{match.group(1)}[REDACTED]{trailing_space}"

    return _LOCAL_PATH.sub(replace, value)


def _redact_query_parameter(match: re.Match[str]) -> str:
    key = match.group(2)
    normalized = _normalized_query_key(key)
    value = "[REDACTED]" if normalized is None or _is_sensitive_key(normalized) else match.group(3)
    return f"{match.group(1)}{key}={value}"


def _invalid_snapshot(value: Any, secrets: tuple[str, ...], depth: int = 0) -> bool:
    if depth >= _MAX_DEPTH:
        return True
    if isinstance(value, Mapping):
        if len(value) > _MAX_ITEMS:
            return True
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128 or any(secret in key for secret in secrets):
                return True
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if _is_sensitive_key(normalized) or _invalid_snapshot(item, secrets, depth + 1):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return len(value) > _MAX_ITEMS or any(_invalid_snapshot(item, secrets, depth + 1) for item in value)
    if isinstance(value, str):
        return (
            len(value) > _MAX_STRING
            or any(secret in value for secret in secrets)
            or any(
                _is_sensitive_key(normalized)
                for match in _QUERY_PARAMETER.finditer(value)
                if (normalized := _normalized_query_key(match.group(2))) is not None
            )
            or any(
                _normalized_query_key(match.group(2)) is None for match in _QUERY_PARAMETER.finditer(value)
            )
        )
    if isinstance(value, float):
        return not math.isfinite(value)
    return value is not None and not isinstance(value, (bool, int))


def _is_sensitive_key(normalized: str) -> bool:
    if normalized == "auth":
        return True
    markers = (
        "authorization",
        "password",
        "passwd",
        "secret",
        "session",
        "cookie",
        "credential",
        "apikey",
        "token",
        "privatekey",
        "accesskey",
        "path",
    )
    return any(normalized.startswith(marker) or normalized.endswith(marker) for marker in markers)


def _normalized_query_key(value: str) -> str | None:
    current = value
    for _ in range(3):
        if re.search(r"%(?![0-9A-Fa-f]{2})", current):
            return None
        try:
            decoded = unquote(current, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        if decoded == current:
            break
        current = decoded
    if "%" in current:
        return None
    return "".join(character for character in current.casefold() if character.isalnum())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("任务时钟必须返回 aware datetime")
    return value.astimezone(UTC)


__all__ = ["TaskRepository", "TaskRepositoryError"]
