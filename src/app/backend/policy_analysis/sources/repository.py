"""Session-bound persistence operations for source-domain services."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from policy_analysis.sources.models import (
    CollectionRule,
    PolicyCategory,
    SeedUrl,
    Source,
)


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_categories(self) -> list[PolicyCategory]:
        statement = select(PolicyCategory).order_by(PolicyCategory.code, PolicyCategory.id)
        return list(self._session.scalars(statement))

    def get_category_by_code(self, code: str) -> PolicyCategory | None:
        return self._session.scalar(select(PolicyCategory).where(PolicyCategory.code == code))

    def get_or_add_category(self, name: str) -> PolicyCategory:
        category = self._session.scalar(
            select(PolicyCategory).where(PolicyCategory.name == name).order_by(PolicyCategory.id)
        )
        if category is None:
            # Stable code prevents concurrent requests from creating duplicate custom categories.
            code = "custom_" + sha256(name.encode("utf-8")).hexdigest()[:56]
            category = PolicyCategory(code=code, name=name, is_active=True)
            self._session.add(category)
            self._session.flush()
        return category

    def list_sources(self) -> list[Source]:
        statement = select(Source).order_by(Source.code, Source.id)
        return list(self._session.scalars(statement))

    def get_source_by_code(self, code: str) -> Source | None:
        return self._session.scalar(select(Source).where(Source.code == code))

    def list_rules(self) -> list[CollectionRule]:
        statement = (
            select(CollectionRule)
            .options(joinedload(CollectionRule.source), joinedload(CollectionRule.category))
            .order_by(CollectionRule.id)
        )
        return list(self._session.scalars(statement))

    def get_rule(self, rule_id: int) -> CollectionRule | None:
        statement = (
            select(CollectionRule)
            .options(joinedload(CollectionRule.source), joinedload(CollectionRule.category))
            .where(CollectionRule.id == rule_id)
        )
        return self._session.scalar(statement)

    def add_rule(self, rule: CollectionRule) -> None:
        self._session.add(rule)

    def existing_seed_urls(self, rule_id: int, urls: Iterable[str]) -> set[str]:
        candidate_urls = tuple(urls)
        if not candidate_urls:
            return set()
        statement = select(SeedUrl.url).where(
            SeedUrl.rule_id == rule_id,
            SeedUrl.url.in_(candidate_urls),
        )
        return set(self._session.scalars(statement))

    def add_seed(self, seed: SeedUrl) -> None:
        self._session.add(seed)
