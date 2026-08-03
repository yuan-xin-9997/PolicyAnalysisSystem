"""Transactional policy deduplication, revisioning, and read services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.core.errors import APIError
from policy_analysis.policies.models import Policy, PolicyRevision
from policy_analysis.policies.repository import PolicyRecord, PolicyRepository
from policy_analysis.policies.schemas import (
    PolicyDetail,
    PolicyListItem,
    PolicyPage,
    PolicyQuery,
    PolicyReferenceRead,
    PolicyUpsertResult,
    PolicyWrite,
)

# The supported deployment is one FastAPI process. This lock makes the
# select-then-insert content deduplication safe across its worker threads.
_WRITE_LOCK = RLock()


class PolicyWriteError(RuntimeError):
    """A sanitized runner-facing write failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PolicyService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._now = now or (lambda: datetime.now(UTC))

    def upsert(self, record: PolicyWrite, task_item_id: int) -> PolicyUpsertResult:
        if type(record) is not PolicyWrite:
            raise TypeError("record must be PolicyWrite")
        if isinstance(task_item_id, bool) or not isinstance(task_item_id, int) or task_item_id < 1:
            raise ValueError("task_item_id must be a positive integer")
        record = _revalidate_record(record)

        try:
            with _WRITE_LOCK, self._sessions.begin() as session:
                repository = PolicyRepository(session)
                if not repository.references_exist(record.source_id, record.category_id, task_item_id):
                    raise _reference_error()
                canonical_url = str(record.canonical_url)
                policy = repository.get_by_source_url(record.source_id, canonical_url)
                if policy is None:
                    policy = repository.get_by_source_hash(record.source_id, record.content_hash)
                if policy is None:
                    policy = Policy(
                        source_id=record.source_id,
                        category_id=record.category_id,
                        title=record.title,
                        canonical_url=canonical_url,
                        publisher=record.publisher,
                        published_at=record.published_at,
                        content_text=record.content_text,
                        content_hash=record.content_hash,
                        webfetch_artifact_id=record.webfetch_artifact_id,
                        first_crawled_at=record.crawled_at,
                        last_crawled_at=record.crawled_at,
                    )
                    repository.add_policy(policy)
                    session.flush()
                    return PolicyUpsertResult(policy_id=policy.id, outcome="stored")

                if policy.content_hash == record.content_hash:
                    if record.crawled_at > policy.last_crawled_at:
                        policy.last_crawled_at = record.crawled_at
                        session.flush()
                    return PolicyUpsertResult(policy_id=policy.id, outcome="duplicate")

                repository.add_revision(
                    PolicyRevision(
                        policy_id=policy.id,
                        content_text=policy.content_text,
                        content_hash=policy.content_hash,
                        webfetch_artifact_id=policy.webfetch_artifact_id,
                        replaced_at=self._aware_now(),
                        task_item_id=task_item_id,
                    )
                )
                policy.category_id = record.category_id
                policy.title = record.title
                policy.publisher = record.publisher
                policy.published_at = record.published_at
                policy.content_text = record.content_text
                policy.content_hash = record.content_hash
                policy.webfetch_artifact_id = record.webfetch_artifact_id
                policy.last_crawled_at = max(policy.last_crawled_at, record.crawled_at)
                policy.updated_at = self._aware_now()
                session.flush()
                return PolicyUpsertResult(policy_id=policy.id, outcome="updated")
        except PolicyWriteError:
            raise
        except IntegrityError:
            raise PolicyWriteError(
                "POLICY_WRITE_CONFLICT",
                "政策写入发生冲突，请重试。",
            ) from None
        except SQLAlchemyError:
            raise PolicyWriteError(
                "POLICY_WRITE_FAILED",
                "政策写入暂时失败。",
            ) from None

    def search(self, query: PolicyQuery) -> PolicyPage:
        with self._sessions() as session:
            records, total = PolicyRepository(session).search(query)
            return PolicyPage(
                items=[_record_to_list_item(record) for record in records],
                total=total,
                page=query.page,
                page_size=query.page_size,
                sort_by=query.sort_by,
                sort_order=query.sort_order,
            )

    def detail(self, policy_id: int) -> PolicyDetail:
        with self._sessions() as session:
            record = PolicyRepository(session).detail(policy_id)
            if record is None:
                raise APIError(status_code=404, code="POLICY_NOT_FOUND", message="政策不存在。")
            item = _record_to_list_item(record)
            return PolicyDetail(**item.model_dump(), content_text=record.policy.content_text)

    def revision_count(self, policy_id: int) -> int:
        with self._sessions() as session:
            return PolicyRepository(session).revision_count(policy_id)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("政策服务时钟必须返回 aware datetime")
        return value.astimezone(UTC)


def _record_to_list_item(record: PolicyRecord) -> PolicyListItem:
    return PolicyListItem(
        id=record.policy.id,
        title=record.policy.title,
        canonical_url=record.policy.canonical_url,
        publisher=record.policy.publisher,
        category=PolicyReferenceRead(
            id=record.category.id,
            code=record.category.code,
            name=record.category.name,
        ),
        source=PolicyReferenceRead(
            id=record.source.id,
            code=record.source.code,
            name=record.source.name,
        ),
        published_at=record.policy.published_at,
        first_crawled_at=record.policy.first_crawled_at,
        last_crawled_at=record.policy.last_crawled_at,
        content_hash=record.policy.content_hash,
        latest_task_id=record.latest_task_id,
    )


def _reference_error() -> PolicyWriteError:
    return PolicyWriteError("POLICY_REFERENCE_INVALID", "政策写入引用无效。")


def _revalidate_record(record: PolicyWrite) -> PolicyWrite:
    payload = record.model_dump(mode="python", warnings=False)
    payload["canonical_url"] = str(record.canonical_url)
    try:
        return PolicyWrite.model_validate(payload)
    except ValidationError:
        raise PolicyWriteError("POLICY_WRITE_INVALID", "政策写入数据无效。") from None
