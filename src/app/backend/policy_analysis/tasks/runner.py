from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.collectors.base import DiscoveredLink, ExtractedArticle, WebFetchClientError
from policy_analysis.collectors.xinhua import XinhuaCollector
from policy_analysis.policies.schemas import PolicyWrite
from policy_analysis.policies.service import PolicyService, PolicyWriteError
from policy_analysis.sources.models import SeedUrl
from policy_analysis.sources.schemas import DiscoveryConfig
from policy_analysis.tasks.models import TaskItemStatus, TaskStatus
from policy_analysis.tasks.repository import TaskRepository, TaskRepositoryError
from policy_analysis.tasks.schemas import TaskRequestSnapshot

BEIJING = ZoneInfo("Asia/Shanghai")


class WebFetch(Protocol):
    def fetch_text(self, url: str) -> str: ...
    def extract_article(self, url: str) -> ExtractedArticle: ...


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    task_id: int
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class _Candidate:
    url: str
    canonical_url: str
    verified_seed: bool = False
    expected_title: str | None = None
    expected_date: date | None = None


@dataclass(frozen=True, slots=True)
class _DiscoveryResult:
    candidates: tuple[_Candidate, ...]
    attempted: int
    failed: int
    cancelled: bool


@dataclass(frozen=True, slots=True)
class _RunConfig:
    rule_active: bool
    source_id: int
    source_active: bool
    category_id: int
    category_active: bool
    adapter_type: str
    allowed_domains: tuple[str, ...]
    history_years: int
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    discovery: DiscoveryConfig
    seeds: tuple[object, ...]


class TaskRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        webfetch: WebFetch,
        policy_service: PolicyService,
        *,
        now: Callable[[], datetime] | None = None,
        minimum_content_chars: int = 100,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._repository = TaskRepository(sessions, secrets=secrets)
        self._webfetch = webfetch
        self._policies = policy_service
        self._now = now or (lambda: datetime.now(UTC))
        self._minimum_content_chars = minimum_content_chars

    def run(self, task_id: int) -> TaskRunResult:
        now = self._aware_now()
        status = self._repository.claim(task_id, now)
        if status is not TaskStatus.RUNNING:
            return TaskRunResult(task_id, status)
        return self.run_claimed(task_id)

    def run_claimed(self, task_id: int) -> TaskRunResult:
        now = self._aware_now()
        try:
            task, rule, seeds = self._repository.load_context(task_id)
            config = _resolve_config(task.request_snapshot_json, rule, seeds)
            if not config.rule_active or not config.source_active or not config.category_active:
                return self._failed(task_id, "采集规则或关联配置未启用。")
            collector = self._build_collector(config)
            cutoff, upper = _rolling_window(now, config.history_years)
            discovered = self._candidates(
                collector,
                config.discovery,
                list(config.seeds),
                cutoff.date(),
                upper.date(),
                task_id,
            )
            candidates = discovered.candidates
            self._repository.set_discovered_count(task_id, len(candidates))
            if discovered.cancelled or self._repository.is_cancel_requested(task_id):
                return TaskRunResult(
                    task_id,
                    self._repository.finish(task_id, TaskStatus.CANCELLED, self._aware_now()),
                )
            verified_failed = False
            ordinary_failed = False
            productive = False
            for candidate in candidates:
                if self._repository.is_cancel_requested(task_id):
                    return TaskRunResult(
                        task_id, self._repository.finish(task_id, TaskStatus.CANCELLED, self._aware_now())
                    )
                item_id = self._repository.create_item(
                    task_id, candidate.url, candidate.canonical_url, self._aware_now()
                )
                try:
                    article = self._webfetch.extract_article(candidate.url)
                    classification = collector.classify(candidate.url, article, cutoff)
                    seed_reason = _seed_mismatch(candidate, classification.title, classification.published_at)
                    if seed_reason:
                        raise _ItemFailure(seed_reason, "已核验种子元数据与页面不一致。")
                    if classification.published_at is not None and classification.published_at > upper:
                        if candidate.verified_seed:
                            raise _ItemFailure("SEED_OUTSIDE_WINDOW", "已核验种子发布时间不在滚动窗口内。")
                        self._repository.finish_item(
                            item_id,
                            TaskItemStatus.FILTERED,
                            self._aware_now(),
                            reason_code="OUTSIDE_WINDOW",
                            reason_message="发布时间不在滚动窗口内。",
                        )
                        productive = True
                        continue
                    if not classification.accepted:
                        if candidate.verified_seed:
                            raise _ItemFailure(
                                f"SEED_{classification.reason_code}",
                                "已核验种子未通过采集判定。",
                            )
                        self._repository.finish_item(
                            item_id,
                            TaskItemStatus.FILTERED,
                            self._aware_now(),
                            reason_code=classification.reason_code,
                            reason_message="候选文章未通过采集判定。",
                        )
                        productive = True
                        continue
                    body = self._paragraph_body(collector, candidate.url, classification.content, task_id)
                    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                    record = PolicyWrite(
                        source_id=config.source_id,
                        category_id=config.category_id,
                        title=classification.title,
                        canonical_url=classification.canonical_url,
                        publisher=classification.publisher,
                        published_at=classification.published_at,
                        content_text=body,
                        content_hash=content_hash,
                        webfetch_artifact_id=classification.artifact_id,
                        crawled_at=self._aware_now(),
                    )
                    self._complete_policy_item(record, item_id)
                    productive = True
                except (
                    WebFetchClientError,
                    PolicyWriteError,
                    _ItemFailure,
                    ValidationError,
                    ValueError,
                ) as exc:
                    code, message = _safe_item_error(exc)
                    self._repository.finish_item(
                        item_id,
                        TaskItemStatus.FAILED,
                        self._aware_now(),
                        reason_code=code,
                        reason_message=message,
                    )
                    verified_failed |= candidate.verified_seed
                    ordinary_failed |= not candidate.verified_seed
                    self._repository.add_log(
                        task_id,
                        "error",
                        "候选文章处理失败。",
                        {"reason_code": code, "candidate_url": candidate.canonical_url},
                    )
                if self._repository.is_cancel_requested(task_id):
                    return TaskRunResult(
                        task_id,
                        self._repository.finish(task_id, TaskStatus.CANCELLED, self._aware_now()),
                    )
            if verified_failed:
                return self._failed(task_id, "已核验历史种子验收失败。")
            if discovered.failed:
                if discovered.failed == discovered.attempted:
                    return self._failed(task_id, "所有发现入口处理失败。")
                if productive:
                    return TaskRunResult(
                        task_id,
                        self._repository.finish(
                            task_id,
                            TaskStatus.PARTIALLY_SUCCEEDED,
                            self._aware_now(),
                            error_summary="部分发现入口处理失败。",
                        ),
                    )
                return self._failed(task_id, "发现入口处理失败。")
            if ordinary_failed:
                terminal = TaskStatus.PARTIALLY_SUCCEEDED if productive else TaskStatus.FAILED
                summary = "部分候选文章处理失败。" if productive else "候选文章处理失败。"
                return TaskRunResult(
                    task_id,
                    self._repository.finish(task_id, terminal, self._aware_now(), error_summary=summary),
                )
            if self._repository.is_cancel_requested(task_id):
                return TaskRunResult(
                    task_id,
                    self._repository.finish(task_id, TaskStatus.CANCELLED, self._aware_now()),
                )
            return TaskRunResult(
                task_id, self._repository.finish(task_id, TaskStatus.SUCCEEDED, self._aware_now())
            )
        except (TaskRepositoryError, ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return self._failed(task_id, "任务配置无效，无法执行采集。")
        except Exception:
            return self._failed(task_id, "采集任务执行失败。")

    def _failed(self, task_id: int, summary: str) -> TaskRunResult:
        try:
            status = self._repository.finish(
                task_id, TaskStatus.FAILED, self._aware_now(), error_summary=summary
            )
        except Exception:
            # Task 8 recovery owns database/process failures that make finalization impossible.
            raise TaskRepositoryError("TASK_FINALIZE_FAILED", "采集任务无法安全结束。") from None
        return TaskRunResult(task_id, status)

    def _complete_policy_item(self, record: PolicyWrite, item_id: int) -> None:
        def finalize(session: Session, outcome) -> None:
            self._repository.finish_item_in_session(
                session,
                item_id,
                TaskItemStatus(outcome.outcome),
                self._aware_now(),
                policy_id=outcome.policy_id,
                reason_code=outcome.outcome.upper(),
                reason_message="候选文章处理完成。",
            )

        self._policies.upsert_and_finalize(record, item_id, finalize)

    def _paragraph_body(
        self,
        collector: XinhuaCollector,
        url: str,
        fallback: str,
        task_id: int,
    ) -> str:
        """Fetch raw HTML and return the cleaned, paragraph-structured body.

        WebFetch's ``generic.article`` adapter flattens the body into a single
        space-joined line, so the raw HTML is fetched separately and parsed into
        ``<p>`` paragraphs. Falls back to ``fallback`` (the inline-cleaned
        flattened content) when the HTML fetch fails or no paragraphs can be
        extracted, so a paragraph-fetch failure never blocks collection of an
        otherwise-accepted article.
        """
        try:
            html = self._webfetch.fetch_text(url)
            body = collector.paragraph_body(html)
        except (WebFetchClientError, ValueError):
            self._repository.add_log(
                task_id,
                "warning",
                "正文段落化抓取失败，回退扁平正文。",
                {"candidate_url": url},
            )
            return fallback
        if not body:
            self._repository.add_log(
                task_id,
                "warning",
                "正文段落解析为空，回退扁平正文。",
                {"candidate_url": url},
            )
            return fallback
        return body

    def _build_collector(self, config: _RunConfig) -> XinhuaCollector:
        if config.adapter_type != "xinhua":
            raise ValueError("ADAPTER_UNSUPPORTED")
        return XinhuaCollector(
            set(config.allowed_domains),
            config.include_keywords,
            config.exclude_keywords,
            self._minimum_content_chars,
        )

    def _candidates(
        self,
        collector: XinhuaCollector,
        discovery: DiscoveryConfig,
        seeds: list[object],
        lower: date,
        upper: date,
        task_id: int,
    ) -> _DiscoveryResult:
        ordered: dict[str, _Candidate] = {}
        attempted = 0
        failed = 0
        for seed in seeds:
            if lower <= seed.expected_published_date <= upper:
                canonical = collector.canonicalize(seed.url)
                candidate = _Candidate(
                    seed.url, canonical, seed.is_verified, seed.expected_title, seed.expected_published_date
                )
                previous = ordered.get(canonical)
                if previous is None or candidate.verified_seed:
                    ordered[canonical] = candidate
        for url in discovery.rss_urls:
            if self._repository.is_cancel_requested(task_id):
                return _DiscoveryResult(tuple(ordered.values()), attempted, failed, True)
            attempted += 1
            try:
                links = collector.discover_from_rss(self._webfetch.fetch_text(url), url)
            except (WebFetchClientError, ValueError):
                failed += 1
                self._repository.add_log(task_id, "error", "RSS 入口发现失败。", {"origin": url})
                continue
            _merge_links(ordered, links, collector)
            if self._repository.is_cancel_requested(task_id):
                return _DiscoveryResult(tuple(ordered.values()), attempted, failed, True)
        for url in discovery.channel_urls:
            if self._repository.is_cancel_requested(task_id):
                return _DiscoveryResult(tuple(ordered.values()), attempted, failed, True)
            attempted += 1
            try:
                links = collector.discover_from_links(
                    _extract_html_links(self._webfetch.fetch_text(url)), url
                )
            except (WebFetchClientError, ValueError):
                failed += 1
                self._repository.add_log(task_id, "error", "栏目入口发现失败。", {"origin": url})
                continue
            _merge_links(ordered, links, collector)
            if self._repository.is_cancel_requested(task_id):
                return _DiscoveryResult(tuple(ordered.values()), attempted, failed, True)
        return _DiscoveryResult(tuple(ordered.values()), attempted, failed, False)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("任务时钟必须返回 aware datetime")
        return value


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a" and self._href is None:
            self._href = next((value for key, value in attrs if key.casefold() == "href" and value), None)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append({"href": self._href, "text": "".join(self._text)})
            self._href = None
            self._text = []


def _extract_html_links(value: str) -> list[dict[str, str]]:
    if not isinstance(value, str) or len(value) > 10_000_000:
        raise ValueError("CHANNEL_INVALID")
    parser = _LinkParser()
    parser.feed(value)
    parser.close()
    return parser.links


def _merge_links(
    ordered: dict[str, _Candidate], links: list[DiscoveredLink], collector: XinhuaCollector
) -> None:
    for link in links:
        canonical = collector.canonicalize(link.url)
        ordered.setdefault(canonical, _Candidate(link.url, canonical))


def _resolve_config(raw_snapshot: str, rule, live_seeds: list[SeedUrl]) -> _RunConfig:
    if raw_snapshot.strip() == "{}":
        return _RunConfig(
            rule_active=rule.is_active,
            source_id=rule.source_id,
            source_active=rule.source.is_active,
            category_id=rule.category_id,
            category_active=rule.category.is_active,
            adapter_type=rule.source.adapter_type,
            allowed_domains=tuple(json.loads(rule.source.allowed_domains_json)),
            history_years=rule.history_years,
            include_keywords=tuple(json.loads(rule.include_keywords_json)),
            exclude_keywords=tuple(json.loads(rule.exclude_keywords_json)),
            discovery=DiscoveryConfig.model_validate_json(rule.discovery_config_json),
            seeds=tuple(live_seeds),
        )
    frozen = TaskRequestSnapshot.model_validate_json(raw_snapshot).rule
    return _RunConfig(
        rule_active=frozen.is_active,
        source_id=frozen.source.id,
        source_active=frozen.source.is_active,
        category_id=frozen.category.id,
        category_active=frozen.category.is_active,
        adapter_type=frozen.source.adapter_type,
        allowed_domains=tuple(frozen.source.allowed_domains),
        history_years=frozen.history_years,
        include_keywords=tuple(frozen.include_keywords),
        exclude_keywords=tuple(frozen.exclude_keywords),
        discovery=DiscoveryConfig.model_validate(frozen.discovery),
        seeds=tuple(frozen.seeds),
    )


def _rolling_window(now: datetime, years: int) -> tuple[datetime, datetime]:
    upper = now.astimezone(BEIJING)
    midnight = upper.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        lower = midnight.replace(year=midnight.year - years)
    except ValueError:
        lower = midnight.replace(year=midnight.year - years, day=28)
    return lower, upper


def _seed_mismatch(candidate: _Candidate, title: str, published_at: datetime | None) -> str | None:
    if not candidate.verified_seed:
        return None
    if " ".join(title.split()) != " ".join((candidate.expected_title or "").split()):
        return "SEED_TITLE_MISMATCH"
    if published_at is None or published_at.astimezone(BEIJING).date() != candidate.expected_date:
        return "SEED_DATE_MISMATCH"
    return None


class _ItemFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_item_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, WebFetchClientError):
        return exc.code, "网页抓取失败。"
    if isinstance(exc, PolicyWriteError):
        return exc.code, "政策写入失败。"
    if isinstance(exc, _ItemFailure):
        return exc.code, str(exc)
    return "CANDIDATE_INVALID", "候选文章数据无效。"


__all__ = ["TaskRunResult", "TaskRunner"]
