from __future__ import annotations

from policy_analysis.tasks.models import TaskStatus

ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.PARTIALLY_SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
}


class TaskStateError(ValueError):
    code = "TASK_STATE_INVALID"


def transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if not isinstance(current, TaskStatus) or not isinstance(target, TaskStatus):
        raise TaskStateError("非法任务状态转换。")
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TaskStateError(f"非法任务状态转换: {current.value} -> {target.value}")
    return target


__all__ = ["TaskStateError", "transition"]
