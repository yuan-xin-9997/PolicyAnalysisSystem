"""Session-bound policy persistence and parameterized search operations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import column, func, or_, select, table, text
from sqlalchemy.orm import Session

from policy_analysis.policies.models import Policy, PolicyRevision
from policy_analysis.policies.schemas import PolicyQuery
from policy_analysis.sources.models import PolicyCategory, Source
from policy_analysis.tasks.models import CrawlTaskItem


@dataclass(frozen=True, slots=True)
class PolicyRecord:
    policy: Policy
    source: Source
    category: PolicyCategory
    latest_task_id: int | None


class PolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_source_url(self, source_id: int, canonical_url: str) -> Policy | None:
        return self._session.scalar(
            select(Policy).where(
                Policy.source_id == source_id,
                Policy.canonical_url == canonical_url,
            )
        )

    def get_by_source_hash(self, source_id: int, content_hash: str) -> Policy | None:
        return self._session.scalar(
            select(Policy)
            .where(
                Policy.source_id == source_id,
                Policy.content_hash == content_hash,
            )
            .order_by(Policy.id)
            .limit(1)
        )

    def references_exist(self, source_id: int, category_id: int, task_item_id: int) -> bool:
        return (
            self._session.get(Source, source_id) is not None
            and self._session.get(PolicyCategory, category_id) is not None
            and self._session.get(CrawlTaskItem, task_item_id) is not None
        )

    def add_policy(self, policy: Policy) -> None:
        self._session.add(policy)

    def add_revision(self, revision: PolicyRevision) -> None:
        self._session.add(revision)

    def revision_count(self, policy_id: int) -> int:
        statement = select(func.count(PolicyRevision.id)).where(PolicyRevision.policy_id == policy_id)
        return int(self._session.scalar(statement) or 0)

    def search(self, query: PolicyQuery) -> tuple[list[PolicyRecord], int]:
        conditions = _search_conditions(query)
        count_statement = select(func.count(Policy.id)).where(*conditions)
        total = int(self._session.scalar(count_statement) or 0)

        latest_task_id = (
            select(CrawlTaskItem.task_id)
            .where(CrawlTaskItem.policy_id == Policy.id)
            .order_by(CrawlTaskItem.id.desc())
            .limit(1)
            .correlate(Policy)
            .scalar_subquery()
        )
        statement = (
            select(Policy, Source, PolicyCategory, latest_task_id.label("latest_task_id"))
            .join(Source, Source.id == Policy.source_id)
            .join(PolicyCategory, PolicyCategory.id == Policy.category_id)
            .where(*conditions)
            .order_by(*_sort_expressions(query))
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        rows = self._session.execute(statement).all()
        return (
            [
                PolicyRecord(
                    policy=row[0],
                    source=row[1],
                    category=row[2],
                    latest_task_id=row[3],
                )
                for row in rows
            ],
            total,
        )

    def detail(self, policy_id: int) -> PolicyRecord | None:
        latest_task_id = (
            select(CrawlTaskItem.task_id)
            .where(CrawlTaskItem.policy_id == Policy.id)
            .order_by(CrawlTaskItem.id.desc())
            .limit(1)
            .correlate(Policy)
            .scalar_subquery()
        )
        statement = (
            select(Policy, Source, PolicyCategory, latest_task_id.label("latest_task_id"))
            .join(Source, Source.id == Policy.source_id)
            .join(PolicyCategory, PolicyCategory.id == Policy.category_id)
            .where(Policy.id == policy_id)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return PolicyRecord(policy=row[0], source=row[1], category=row[2], latest_task_id=row[3])


def _search_conditions(query: PolicyQuery) -> list[object]:
    conditions: list[object] = []
    if query.keyword is not None:
        conditions.append(_keyword_condition(query.keyword))
    if query.published_from is not None:
        conditions.append(Policy.published_at >= query.published_from)
    if query.published_to is not None:
        conditions.append(Policy.published_at <= query.published_to)
    if query.crawled_from is not None:
        conditions.append(Policy.last_crawled_at >= query.crawled_from)
    if query.crawled_to is not None:
        conditions.append(Policy.last_crawled_at <= query.crawled_to)
    if query.publisher is not None:
        conditions.append(Policy.publisher == query.publisher)
    if query.category_id is not None:
        conditions.append(Policy.category_id == query.category_id)
    if query.source_id is not None:
        conditions.append(Policy.source_id == query.source_id)
    return conditions


def _keyword_condition(keyword: str) -> object:
    # FTS5 trigram does not index one- or two-character terms. The escaped LIKE
    # fallback keeps those common Chinese searches literal and parameterized.
    if len(keyword) < 3:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        return or_(
            Policy.title.like(pattern, escape="\\"),
            Policy.content_text.like(pattern, escape="\\"),
        )

    fts = table("policies_fts", column("rowid"))
    literal_phrase = f'"{keyword.replace(chr(34), chr(34) * 2)}"'
    match = text("policies_fts MATCH :fts_query").bindparams(fts_query=literal_phrase)
    return Policy.id.in_(select(fts.c.rowid).where(match))


def _sort_expressions(query: PolicyQuery) -> tuple[object, object]:
    sort_column = {
        "published_at": Policy.published_at,
        "last_crawled_at": Policy.last_crawled_at,
    }[query.sort_by]
    direction = "asc" if query.sort_order == "asc" else "desc"
    return getattr(sort_column, direction)(), getattr(Policy.id, direction)()
