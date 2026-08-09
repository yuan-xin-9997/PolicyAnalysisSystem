import pytest
from policy_analysis.analysis.models import AnalysisTaskStatus
from policy_analysis.analysis.state import AnalysisStateError, transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AnalysisTaskStatus.PENDING, AnalysisTaskStatus.RUNNING),
        (AnalysisTaskStatus.RUNNING, AnalysisTaskStatus.SUCCEEDED),
        (AnalysisTaskStatus.RUNNING, AnalysisTaskStatus.FAILED),
    ],
)
def test_analysis_state_allows_documented_transitions(
    current: AnalysisTaskStatus, target: AnalysisTaskStatus
) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AnalysisTaskStatus.PENDING, AnalysisTaskStatus.SUCCEEDED),
        (AnalysisTaskStatus.PENDING, AnalysisTaskStatus.FAILED),
        (AnalysisTaskStatus.RUNNING, AnalysisTaskStatus.PENDING),
        (AnalysisTaskStatus.SUCCEEDED, AnalysisTaskStatus.RUNNING),
    ],
)
def test_analysis_state_rejects_other_transitions(
    current: AnalysisTaskStatus, target: AnalysisTaskStatus
) -> None:
    with pytest.raises(AnalysisStateError, match="非法分析任务状态转换") as raised:
        transition(current, target)
    assert raised.value.code == "ANALYSIS_STATE_INVALID"


def test_analysis_state_rejects_non_enum_values() -> None:
    with pytest.raises(AnalysisStateError, match="非法分析任务状态转换"):
        transition("pending", AnalysisTaskStatus.RUNNING)  # type: ignore[arg-type]
