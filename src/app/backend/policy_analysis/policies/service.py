"""Transactional policy deduplication, revisioning, and read services."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.core.errors import APIError
from policy_analysis.policies.models import Policy, PolicyRevision
from policy_analysis.policies.repository import PolicyRecord, PolicyRepository
from policy_analysis.policies.schemas import (
    PolicyDetail,
    PolicyFilterOptions,
    PolicyListItem,
    PolicyPage,
    PolicyQuery,
    PolicyReferenceRead,
    PolicyUpsertResult,
    PolicyWrite,
)
from policy_analysis.sources.models import PolicyCategory, Source

# The supported deployment is one FastAPI process. This lock makes the
# select-then-insert content deduplication safe across its worker threads.
_WRITE_LOCK = RLock()

# Beijing time is the canonical "news day" for the policy meeting key: a
# meeting announced at 23:00 Beijing on 2022-04-26 must hash to the same
# date even if its UTC timestamp crosses midnight. Using the Beijing date
# keeps the meeting identity stable across collectors and DB storage.
_BEIJING = ZoneInfo("Asia/Shanghai")

# Source priority for cross-source meeting dedup. A higher number wins when
# the meeting_key lookup finds an existing policy: the new (higher-priority)
# source's content replaces the old one, the old content is preserved as a
# PolicyRevision, and the outcome is "updated". A lower or equal priority
# just records "duplicate" and leaves the existing policy alone.
_XINHUA_DOMAINS = frozenset({"news.cn", "xinhuanet.com"})
_PEOPLE_CN_DOMAIN = "people.com.cn"
_CCTV_DOMAIN = "cctv.com"


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
        record = _validated_upsert_input(record, task_item_id)
        try:
            with _WRITE_LOCK, self._sessions.begin() as session:
                return self._upsert(session, record, task_item_id)
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

    def upsert_and_finalize(
        self,
        record: PolicyWrite,
        task_item_id: int,
        finalize: Callable[[Session, PolicyUpsertResult], None],
    ) -> PolicyUpsertResult:
        """Hold the process write lock through finalization and transaction commit."""
        if not callable(finalize):
            raise TypeError("finalize must be callable")
        record = _validated_upsert_input(record, task_item_id)
        try:
            with _WRITE_LOCK, self._sessions.begin() as session:
                result = self._upsert(session, record, task_item_id)
                try:
                    finalize(session, result)
                except SQLAlchemyError:
                    raise PolicyWriteError("POLICY_WRITE_FAILED", "政策写入暂时失败。") from None
                return result
        except PolicyWriteError:
            raise
        except IntegrityError:
            raise PolicyWriteError("POLICY_WRITE_CONFLICT", "政策写入发生冲突，请重试。") from None
        except SQLAlchemyError:
            raise PolicyWriteError("POLICY_WRITE_FAILED", "政策写入暂时失败。") from None

    def _upsert(self, session: Session, record: PolicyWrite, task_item_id: int) -> PolicyUpsertResult:
        repository = PolicyRepository(session)
        if not repository.references_exist(record.source_id, record.category_id, task_item_id):
            raise _reference_error()
        canonical_url = str(record.canonical_url)

        # 1) Cross-source meeting-key dedup. Two sources reporting the same
        # meeting carry the same (category, title, Beijing date) even when
        # their bodies differ in page chrome, so a global lookup on this
        # triple is the primary cross-source dedup path.
        existing_source = session.get(Source, record.source_id)
        beijing_date = record.published_at.astimezone(_BEIJING).date()
        existing_meeting = repository.get_by_meeting_key(record.category_id, record.title, beijing_date)
        if (
            existing_meeting is not None
            and existing_source is not None
            and existing_meeting.source_id != record.source_id
        ):
            existing_priority = _source_priority_for_source(session, existing_meeting.source_id)
            new_priority = _source_priority(existing_source)
            if new_priority > existing_priority:
                return self._upgrade_meeting(session, repository, existing_meeting, record, task_item_id)
            return PolicyUpsertResult(policy_id=existing_meeting.id, outcome="duplicate")

        # 2) Source-scoped dedup (existing behaviour).
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

    def _upgrade_meeting(
        self,
        session: Session,
        repository: PolicyRepository,
        policy: Policy,
        record: PolicyWrite,
        task_item_id: int,
    ) -> PolicyUpsertResult:
        """Replace a lower-priority source's content with a higher-priority one."""

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
        policy.source_id = record.source_id
        policy.category_id = record.category_id
        policy.title = record.title
        policy.canonical_url = str(record.canonical_url)
        policy.publisher = record.publisher
        policy.published_at = record.published_at
        policy.content_text = record.content_text
        policy.content_hash = record.content_hash
        policy.webfetch_artifact_id = record.webfetch_artifact_id
        policy.last_crawled_at = max(policy.last_crawled_at, record.crawled_at)
        policy.updated_at = self._aware_now()
        session.flush()
        return PolicyUpsertResult(policy_id=policy.id, outcome="updated")

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

    def filter_options(self) -> PolicyFilterOptions:
        with self._sessions() as session:
            publishers, categories, sources = PolicyRepository(session).filter_options()
            return PolicyFilterOptions(
                publishers=publishers,
                categories=[_reference_to_read(category) for category in categories],
                sources=[_reference_to_read(source) for source in sources],
            )

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
        category=_reference_to_read(record.category),
        source=_reference_to_read(record.source),
        published_at=record.policy.published_at,
        first_crawled_at=record.policy.first_crawled_at,
        last_crawled_at=record.policy.last_crawled_at,
        content_hash=record.policy.content_hash,
        latest_task_id=record.latest_task_id,
    )


def _reference_to_read(reference: PolicyCategory | Source) -> PolicyReferenceRead:
    return PolicyReferenceRead(
        id=reference.id,
        code=reference.code,
        name=reference.name,
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


def _validated_upsert_input(record: PolicyWrite, task_item_id: int) -> PolicyWrite:
    if type(record) is not PolicyWrite:
        raise TypeError("record must be PolicyWrite")
    if isinstance(task_item_id, bool) or not isinstance(task_item_id, int) or task_item_id < 1:
        raise ValueError("task_item_id must be a positive integer")
    return _revalidate_record(record)


def _source_priority(source: Source) -> int:
    """Return a higher number for the more authoritative source.

    Used to decide which source wins when the same meeting is reported by
    multiple sources. Xinhua wire (news.cn / xinhuanet.com) wins, then
    People's Daily (people.com.cn), then CCTV (cctv.com). Unknown sources
    are lowest priority so they never overwrite a known source.
    """
    domains = _allowed_domains(source)
    if domains & _XINHUA_DOMAINS:
        return 3
    if _PEOPLE_CN_DOMAIN in domains:
        return 2
    if _CCTV_DOMAIN in domains:
        return 1
    return 0


def _source_priority_for_source(session: Session, source_id: int) -> int:
    source = session.get(Source, source_id)
    if source is None:
        return 0
    return _source_priority(source)


def _allowed_domains(source: Source) -> set[str]:
    try:
        return set(json.loads(source.allowed_domains_json))
    except (TypeError, ValueError):
        return set()
