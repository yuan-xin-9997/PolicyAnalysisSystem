import pytest
from policy_analysis.tasks.models import TaskStatus
from policy_analysis.tasks.state import TaskStateError, transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.PARTIALLY_SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
    ],
)
def test_task_state_allows_only_documented_transitions(current: TaskStatus, target: TaskStatus) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.PENDING),
        (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),
        (TaskStatus.CANCELLED, TaskStatus.CANCELLED),
    ],
)
def test_task_state_rejects_other_transitions_without_internal_details(
    current: TaskStatus, target: TaskStatus
) -> None:
    with pytest.raises(TaskStateError, match="非法任务状态转换") as raised:
        transition(current, target)
    assert raised.value.code == "TASK_STATE_INVALID"
    assert "SQL" not in str(raised.value)


def test_task_state_rejects_non_enum_values_with_stable_error() -> None:
    with pytest.raises(TaskStateError, match="非法任务状态转换") as raised:
        transition("pending", TaskStatus.RUNNING)  # type: ignore[arg-type]
    assert raised.value.code == "TASK_STATE_INVALID"
