from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from policy_analysis.auth.models import UTCDateTime, _utc_now
from policy_analysis.core.database import Base


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_policies_source_canonical_url",
        ),
        Index("ix_policies_source_content_hash", "source_id", "content_hash"),
        Index("ix_policies_published_at", "published_at"),
        Index("ix_policies_last_crawled_at", "last_crawled_at"),
        Index("ix_policies_publisher", "publisher"),
        Index("ix_policies_category_id", "category_id"),
        # Cross-source meeting-key dedup: same (category_id, title, published_at)
        # is the same meeting regardless of which source reported it.
        Index(
            "ix_policies_category_title_published",
            "category_id",
            "title",
            "published_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", name="fk_policies_source_id_sources"),
        nullable=False,
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("policy_categories.id", name="fk_policies_category_id_policy_categories"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    publisher: Mapped[str] = mapped_column(String(256), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    webfetch_artifact_id: Mapped[str] = mapped_column(String(256), nullable=False)
    first_crawled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_crawled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class PolicyRevision(Base):
    __tablename__ = "policy_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey(
            "policies.id",
            ondelete="CASCADE",
            name="fk_policy_revisions_policy_id_policies",
        ),
        nullable=False,
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    webfetch_artifact_id: Mapped[str] = mapped_column(String(256), nullable=False)
    replaced_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)
    task_item_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "crawl_task_items.id",
            ondelete="SET NULL",
            name="fk_policy_revisions_task_item_id_crawl_task_items",
        ),
        nullable=True,
    )
