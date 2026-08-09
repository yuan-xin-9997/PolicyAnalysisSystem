"""Create policy analysis domain tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_type", sa.String(length=32), server_default=sa.text("'word_frequency'"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("policy_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
        sa.Column("request_snapshot_json", sa.Text(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "task_type IN ('word_frequency')",
            name="ck_analysis_tasks_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_analysis_tasks_status",
        ),
        sa.CheckConstraint(
            "length(trim(request_snapshot_json)) > 0",
            name="ck_analysis_tasks_request_snapshot_nonempty",
        ),
        sa.CheckConstraint(
            "policy_count >= 0",
            name="ck_analysis_tasks_policy_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_analysis_tasks_requested_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "analysis_task_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name="fk_analysis_task_policies_policy_id_policies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            name="fk_analysis_task_policies_task_id_analysis_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "policy_id", name="uq_analysis_task_policies_task_policy"),
    )
    op.create_index("ix_analysis_task_policies_task_id", "analysis_task_policies", ["task_id"])
    op.create_table(
        "analysis_word_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("word", sa.String(length=128), nullable=False),
        sa.Column("word_type", sa.String(length=16), nullable=True),
        sa.Column("frequency", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tfidf", sa.Float(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "frequency >= 0",
            name="ck_analysis_word_results_frequency_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name="fk_analysis_word_results_policy_id_policies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            name="fk_analysis_word_results_task_id_analysis_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "policy_id", "word", name="uq_analysis_word_results_task_policy_word"),
    )
    op.create_index("ix_analysis_word_results_task_word", "analysis_word_results", ["task_id", "word"])
    op.create_index("ix_analysis_word_results_task_policy", "analysis_word_results", ["task_id", "policy_id"])
    op.create_table(
        "analysis_word_relations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("word1", sa.String(length=128), nullable=False),
        sa.Column("word2", sa.String(length=128), nullable=False),
        sa.Column("co_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "co_count >= 0",
            name="ck_analysis_word_relations_co_count_nonnegative",
        ),
        sa.CheckConstraint(
            "word1 < word2",
            name="ck_analysis_word_relations_word_order",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            name="fk_analysis_word_relations_task_id_analysis_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "word1", "word2", name="uq_analysis_word_relations_task_words"),
    )
    op.create_index("ix_analysis_word_relations_task_id", "analysis_word_relations", ["task_id"])
    op.create_table(
        "analysis_task_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(trim(context_json)) > 0",
            name="ck_analysis_task_logs_context_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            name="fk_analysis_task_logs_task_id_analysis_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("analysis_task_logs")
    op.drop_index("ix_analysis_word_relations_task_id", table_name="analysis_word_relations")
    op.drop_table("analysis_word_relations")
    op.drop_index("ix_analysis_word_results_task_policy", table_name="analysis_word_results")
    op.drop_index("ix_analysis_word_results_task_word", table_name="analysis_word_results")
    op.drop_table("analysis_word_results")
    op.drop_index("ix_analysis_task_policies_task_id", table_name="analysis_task_policies")
    op.drop_table("analysis_task_policies")
    op.drop_table("analysis_tasks")
