"""Merge per-rule scheduling into collection_rules and drop schedules.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-23

Collection rules now carry their trigger mode directly: ``trigger_mode`` is
either ``manual`` (only manual triggers) or ``schedule`` (cron-driven runs
that can also be triggered manually). The scheduling columns previously
stored in the separate ``schedules`` table (one representative row per rule,
preferring active ones) are folded into ``collection_rules`` and the table is
dropped. New columns intentionally omit table-level CHECK constraints:
SQLite cannot attach them via ALTER TABLE, and values are validated at the
service boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPRESENTATIVE_SCHEDULE = (
    "(SELECT s.{column} FROM schedules s WHERE s.rule_id = collection_rules.id "
    "ORDER BY s.is_active DESC, s.id LIMIT 1)"
)


def upgrade() -> None:
    op.add_column(
        "collection_rules",
        sa.Column("trigger_mode", sa.String(length=16), server_default=sa.text("'manual'"), nullable=False),
    )
    op.add_column("collection_rules", sa.Column("cron_expression", sa.String(length=128), nullable=True))
    op.add_column(
        "collection_rules",
        sa.Column(
            "schedule_timezone",
            sa.String(length=64),
            server_default=sa.text("'Asia/Shanghai'"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_rules",
        sa.Column("schedule_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("collection_rules", sa.Column("next_run_at", sa.String(length=40), nullable=True))
    op.add_column("collection_rules", sa.Column("last_run_at", sa.String(length=40), nullable=True))

    op.get_bind().execute(
        text(
            "UPDATE collection_rules SET "
            "trigger_mode = 'schedule', "
            f"cron_expression = {_REPRESENTATIVE_SCHEDULE.format(column='cron_expression')}, "
            f"schedule_timezone = COALESCE({_REPRESENTATIVE_SCHEDULE.format(column='timezone')}, 'Asia/Shanghai'), "
            f"schedule_enabled = COALESCE({_REPRESENTATIVE_SCHEDULE.format(column='is_active')}, 0), "
            f"next_run_at = {_REPRESENTATIVE_SCHEDULE.format(column='next_run_at')}, "
            f"last_run_at = {_REPRESENTATIVE_SCHEDULE.format(column='last_run_at')} "
            "WHERE EXISTS (SELECT 1 FROM schedules s WHERE s.rule_id = collection_rules.id)"
        )
    )
    op.drop_table("schedules")


def downgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("cron_expression", sa.String(length=128), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default=sa.text("'Asia/Shanghai'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_run_at", sa.String(length=40), nullable=True),
        sa.Column("last_run_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["collection_rules.id"],
            name="fk_schedules_rule_id_collection_rules",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.drop_column("collection_rules", "last_run_at")
    op.drop_column("collection_rules", "next_run_at")
    op.drop_column("collection_rules", "schedule_enabled")
    op.drop_column("collection_rules", "schedule_timezone")
    op.drop_column("collection_rules", "cron_expression")
    op.drop_column("collection_rules", "trigger_mode")
