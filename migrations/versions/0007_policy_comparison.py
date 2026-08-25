"""Add policy comparison analysis reports.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_tasks") as batch_op:
        batch_op.drop_constraint("ck_analysis_tasks_task_type", type_="check")
        batch_op.create_check_constraint(
            "ck_analysis_tasks_task_type",
            "task_type IN ('word_frequency', 'policy_comparison')",
        )
    op.create_table(
        "analysis_comparison_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            name="fk_analysis_comparison_reports_task_id_analysis_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("analysis_comparison_reports")
    with op.batch_alter_table("analysis_tasks") as batch_op:
        batch_op.drop_constraint("ck_analysis_tasks_task_type", type_="check")
        batch_op.create_check_constraint(
            "ck_analysis_tasks_task_type", "task_type IN ('word_frequency')"
        )
