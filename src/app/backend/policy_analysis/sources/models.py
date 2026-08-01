from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from policy_analysis.auth.models import UTCDateTime, _utc_now
from policy_analysis.core.database import Base


class PolicyCategory(Base):
    __tablename__ = "policy_categories"
    __table_args__ = (UniqueConstraint("code", name="uq_policy_categories_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("1"),
        nullable=False,
    )


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("code", name="uq_sources_code"),
        CheckConstraint(
            "length(trim(allowed_domains_json)) > 0",
            name="ck_sources_allowed_domains_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    organization: Mapped[str] = mapped_column(String(256), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_domains_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("1"),
        nullable=False,
    )


class CollectionRule(Base):
    __tablename__ = "collection_rules"
    __table_args__ = (
        CheckConstraint(
            "history_years BETWEEN 1 AND 20",
            name="ck_collection_rules_history_years",
        ),
        CheckConstraint(
            "length(trim(include_keywords_json)) > 0",
            name="ck_collection_rules_include_keywords_nonempty",
        ),
        CheckConstraint(
            "length(trim(exclude_keywords_json)) > 0",
            name="ck_collection_rules_exclude_keywords_nonempty",
        ),
        CheckConstraint(
            "length(trim(discovery_config_json)) > 0",
            name="ck_collection_rules_discovery_config_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", name="fk_collection_rules_source_id_sources"),
        nullable=False,
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("policy_categories.id", name="fk_collection_rules_category_id_policy_categories"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    include_keywords_json: Mapped[str] = mapped_column(Text, nullable=False)
    exclude_keywords_json: Mapped[str] = mapped_column(Text, nullable=False)
    history_years: Mapped[int] = mapped_column(
        Integer,
        default=5,
        server_default=text("5"),
        nullable=False,
    )
    discovery_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("1"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )

    source: Mapped[Source] = relationship()
    category: Mapped[PolicyCategory] = relationship()


class SeedUrl(Base):
    __tablename__ = "seed_urls"
    __table_args__ = (UniqueConstraint("rule_id", "url", name="uq_seed_urls_rule_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collection_rules.id",
            ondelete="CASCADE",
            name="fk_seed_urls_rule_id_collection_rules",
        ),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    expected_title: Mapped[str] = mapped_column(String(512), nullable=False)
    expected_published_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utc_now, nullable=False)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collection_rules.id",
            ondelete="CASCADE",
            name="fk_schedules_rule_id_collection_rules",
        ),
        nullable=False,
    )
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Shanghai",
        server_default=text("'Asia/Shanghai'"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
        nullable=False,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
