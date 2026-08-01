"""Create collection domain tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_policy_categories_code"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("organization", sa.String(length=256), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("adapter_type", sa.String(length=128), nullable=False),
        sa.Column("allowed_domains_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "length(trim(allowed_domains_json)) > 0",
            name="ck_sources_allowed_domains_nonempty",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )
    op.create_table(
        "collection_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("include_keywords_json", sa.Text(), nullable=False),
        sa.Column("exclude_keywords_json", sa.Text(), nullable=False),
        sa.Column("history_years", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("discovery_config_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "history_years BETWEEN 1 AND 20",
            name="ck_collection_rules_history_years",
        ),
        sa.CheckConstraint(
            "length(trim(include_keywords_json)) > 0",
            name="ck_collection_rules_include_keywords_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(exclude_keywords_json)) > 0",
            name="ck_collection_rules_exclude_keywords_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(discovery_config_json)) > 0",
            name="ck_collection_rules_discovery_config_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["policy_categories.id"],
            name="fk_collection_rules_category_id_policy_categories",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_collection_rules_source_id_sources",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "seed_urls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("expected_title", sa.String(length=512), nullable=False),
        sa.Column("expected_published_date", sa.Date(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["collection_rules.id"],
            name="fk_seed_urls_rule_id_collection_rules",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "url", name="uq_seed_urls_rule_url"),
    )
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
    op.create_table(
        "policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("publisher", sa.String(length=256), nullable=False),
        sa.Column("published_at", sa.String(length=40), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("webfetch_artifact_id", sa.String(length=256), nullable=False),
        sa.Column("first_crawled_at", sa.String(length=40), nullable=False),
        sa.Column("last_crawled_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["policy_categories.id"],
            name="fk_policies_category_id_policy_categories",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_policies_source_id_sources",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_policies_source_canonical_url",
        ),
    )
    op.create_index("ix_policies_source_content_hash", "policies", ["source_id", "content_hash"])
    op.create_index("ix_policies_published_at", "policies", ["published_at"])
    op.create_index("ix_policies_last_crawled_at", "policies", ["last_crawled_at"])
    op.create_index("ix_policies_publisher", "policies", ["publisher"])
    op.create_index("ix_policies_category_id", "policies", ["category_id"])
    op.create_table(
        "crawl_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("scheduled_for", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
        sa.Column("cancel_requested_at", sa.String(length=40), nullable=True),
        sa.Column("request_snapshot_json", sa.Text(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("success_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("filtered_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "trigger_type IN ('manual', 'schedule')",
            name="ck_crawl_tasks_trigger_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'partially_succeeded', 'failed', 'cancelled')",
            name="ck_crawl_tasks_status",
        ),
        sa.CheckConstraint(
            "length(trim(request_snapshot_json)) > 0",
            name="ck_crawl_tasks_request_snapshot_nonempty",
        ),
        sa.CheckConstraint(
            "discovered_count >= 0",
            name="ck_crawl_tasks_discovered_count_nonnegative",
        ),
        sa.CheckConstraint("success_count >= 0", name="ck_crawl_tasks_success_count_nonnegative"),
        sa.CheckConstraint("duplicate_count >= 0", name="ck_crawl_tasks_duplicate_count_nonnegative"),
        sa.CheckConstraint("filtered_count >= 0", name="ck_crawl_tasks_filtered_count_nonnegative"),
        sa.CheckConstraint("failed_count >= 0", name="ck_crawl_tasks_failed_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_crawl_tasks_requested_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["collection_rules.id"],
            name="fk_crawl_tasks_rule_id_collection_rules",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crawl_task_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("candidate_url", sa.String(length=2048), nullable=False),
        sa.Column("normalized_url", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("reason_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "status IN ('stored', 'updated', 'duplicate', 'filtered', 'failed')",
            name="ck_crawl_task_items_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_crawl_task_items_attempt_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name="fk_crawl_task_items_policy_id_policies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["crawl_tasks.id"],
            name="fk_crawl_task_items_task_id_crawl_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "policy_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("webfetch_artifact_id", sa.String(length=256), nullable=False),
        sa.Column("replaced_at", sa.String(length=40), nullable=False),
        sa.Column("task_item_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name="fk_policy_revisions_policy_id_policies",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_item_id"],
            ["crawl_task_items.id"],
            name="fk_policy_revisions_task_item_id_crawl_task_items",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crawl_task_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(trim(context_json)) > 0",
            name="ck_crawl_task_logs_context_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["crawl_tasks.id"],
            name="fk_crawl_task_logs_task_id_crawl_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("crawl_task_logs")
    op.drop_table("policy_revisions")
    op.drop_table("crawl_task_items")
    op.drop_table("crawl_tasks")
    op.drop_index("ix_policies_category_id", table_name="policies")
    op.drop_index("ix_policies_publisher", table_name="policies")
    op.drop_index("ix_policies_last_crawled_at", table_name="policies")
    op.drop_index("ix_policies_published_at", table_name="policies")
    op.drop_index("ix_policies_source_content_hash", table_name="policies")
    op.drop_table("policies")
    op.drop_table("schedules")
    op.drop_table("seed_urls")
    op.drop_table("collection_rules")
    op.drop_table("sources")
    op.drop_table("policy_categories")
