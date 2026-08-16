"""Add cross-source meeting-key dedup index on policies.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-15

Adds ``ix_policies_category_title_published`` on
``(category_id, title, published_at)`` to support cross-source deduplication
of the same meeting when it is reported by multiple sources (e.g. news.cn,
people.com.cn, cctv.com). The index is non-unique; the dedup is a select-
then-update under the process write lock, not a DB-level constraint.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_policies_category_title_published",
        "policies",
        ["category_id", "title", "published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_policies_category_title_published", table_name="policies")
