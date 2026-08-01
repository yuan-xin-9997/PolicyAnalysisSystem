from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from policy_analysis.auth.models import UTCDateTime, _utc_now
from policy_analysis.core.database import Base


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskItemStatus(StrEnum):
    STORED = "stored"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    FILTERED = "filtered"
    FAILED = "failed"


class CrawlTask(Base):
    __tablename__ = "crawl_tasks"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('manual', 'schedule')",
            name="ck_crawl_tasks_trigger_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'partially_succeeded', 'failed', 'cancelled')",
            name="ck_crawl_tasks_status",
        ),
        CheckConstraint(
            "length(trim(request_snapshot_json)) > 0",
            name="ck_crawl_tasks_request_snapshot_nonempty",
        ),
        CheckConstraint(
            "discovered_count >= 0",
            name="ck_crawl_tasks_discovered_count_nonnegative",
        ),
        CheckConstraint("success_count >= 0", name="ck_crawl_tasks_success_count_nonnegative"),
        CheckConstraint("duplicate_count >= 0", name="ck_crawl_tasks_duplicate_count_nonnegative"),
        CheckConstraint("filtered_count >= 0", name="ck_crawl_tasks_filtered_count_nonnegative"),
        CheckConstraint("failed_count >= 0", name="ck_crawl_tasks_failed_count_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("collection_rules.id", name="fk_crawl_tasks_rule_id_collection_rules"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_crawl_tasks_requested_by_users",
        ),
        nullable=True,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    request_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class CrawlTaskItem(Base):
    __tablename__ = "crawl_task_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('stored', 'updated', 'duplicate', 'filtered', 'failed')",
            name="ck_crawl_task_items_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_crawl_task_items_attempt_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "crawl_tasks.id",
            ondelete="CASCADE",
            name="fk_crawl_task_items_task_id_crawl_tasks",
        ),
        nullable=False,
    )
    candidate_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "policies.id",
            ondelete="SET NULL",
            name="fk_crawl_task_items_policy_id_policies",
        ),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CrawlTaskLog(Base):
    __tablename__ = "crawl_task_logs"
    __table_args__ = (
        CheckConstraint(
            "length(trim(context_json)) > 0",
            name="ck_crawl_task_logs_context_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "crawl_tasks.id",
            ondelete="CASCADE",
            name="fk_crawl_task_logs_task_id_crawl_tasks",
        ),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)
