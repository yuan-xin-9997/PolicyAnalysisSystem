from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from policy_analysis.auth.models import UTCDateTime, _utc_now
from policy_analysis.core.database import Base

WORD_VALUE = "word_frequency"
COMPARISON_VALUE = "policy_comparison"


class AnalysisTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('word_frequency', 'policy_comparison')",
            name="ck_analysis_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_analysis_tasks_status",
        ),
        CheckConstraint(
            "length(trim(request_snapshot_json)) > 0",
            name="ck_analysis_tasks_request_snapshot_nonempty",
        ),
        CheckConstraint(
            "policy_count >= 0",
            name="ck_analysis_tasks_policy_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(
        String(32),
        default=WORD_VALUE,
        server_default=text("'word_frequency'"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=AnalysisTaskStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_analysis_tasks_requested_by_users",
        ),
        nullable=True,
    )
    policy_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    request_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class AnalysisTaskPolicy(Base):
    __tablename__ = "analysis_task_policies"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "policy_id",
            name="uq_analysis_task_policies_task_policy",
        ),
        Index("ix_analysis_task_policies_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            name="fk_analysis_task_policies_task_id_analysis_tasks",
        ),
        nullable=False,
    )
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "policies.id",
            ondelete="SET NULL",
            name="fk_analysis_task_policies_policy_id_policies",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class AnalysisWordResult(Base):
    __tablename__ = "analysis_word_results"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "policy_id",
            "word",
            name="uq_analysis_word_results_task_policy_word",
        ),
        Index("ix_analysis_word_results_task_word", "task_id", "word"),
        Index("ix_analysis_word_results_task_policy", "task_id", "policy_id"),
        CheckConstraint(
            "frequency >= 0",
            name="ck_analysis_word_results_frequency_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            name="fk_analysis_word_results_task_id_analysis_tasks",
        ),
        nullable=False,
    )
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "policies.id",
            ondelete="SET NULL",
            name="fk_analysis_word_results_policy_id_policies",
        ),
        nullable=True,
    )
    word: Mapped[str] = mapped_column(String(128), nullable=False)
    word_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    frequency: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    tfidf: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class AnalysisWordRelation(Base):
    __tablename__ = "analysis_word_relations"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "word1",
            "word2",
            name="uq_analysis_word_relations_task_words",
        ),
        Index("ix_analysis_word_relations_task_id", "task_id"),
        CheckConstraint(
            "co_count >= 0",
            name="ck_analysis_word_relations_co_count_nonnegative",
        ),
        CheckConstraint(
            "word1 < word2",
            name="ck_analysis_word_relations_word_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            name="fk_analysis_word_relations_task_id_analysis_tasks",
        ),
        nullable=False,
    )
    word1: Mapped[str] = mapped_column(String(128), nullable=False)
    word2: Mapped[str] = mapped_column(String(128), nullable=False)
    co_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class AnalysisTaskLog(Base):
    __tablename__ = "analysis_task_logs"
    __table_args__ = (
        CheckConstraint(
            "length(trim(context_json)) > 0",
            name="ck_analysis_task_logs_context_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            name="fk_analysis_task_logs_task_id_analysis_tasks",
        ),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class AnalysisComparisonReport(Base):
    __tablename__ = "analysis_comparison_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            name="fk_analysis_comparison_reports_task_id_analysis_tasks",
        ),
        unique=True,
        nullable=False,
    )
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)
