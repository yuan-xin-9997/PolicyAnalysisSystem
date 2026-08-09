from __future__ import annotations

from policy_analysis.analysis.models import AnalysisTaskStatus

ALLOWED_TRANSITIONS = {
    AnalysisTaskStatus.PENDING: frozenset({AnalysisTaskStatus.RUNNING}),
    AnalysisTaskStatus.RUNNING: frozenset({AnalysisTaskStatus.SUCCEEDED, AnalysisTaskStatus.FAILED}),
}


class AnalysisStateError(ValueError):
    code = "ANALYSIS_STATE_INVALID"


def transition(current: AnalysisTaskStatus, target: AnalysisTaskStatus) -> AnalysisTaskStatus:
    if not isinstance(current, AnalysisTaskStatus) or not isinstance(target, AnalysisTaskStatus):
        raise AnalysisStateError("非法分析任务状态转换。")
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise AnalysisStateError(f"非法分析任务状态转换: {current.value} -> {target.value}")
    return target


__all__ = ["ALLOWED_TRANSITIONS", "AnalysisStateError", "transition"]
