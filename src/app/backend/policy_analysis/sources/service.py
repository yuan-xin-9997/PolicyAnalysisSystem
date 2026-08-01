"""Business rules and short transaction boundaries for collection sources."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.core.errors import APIError
from policy_analysis.sources.models import CollectionRule, PolicyCategory, Schedule, SeedUrl, Source
from policy_analysis.sources.repository import SourceRepository
from policy_analysis.sources.schemas import (
    CollectionRuleCreate,
    CollectionRuleRead,
    CollectionRuleUpdate,
    DiscoveryConfig,
    PolicyCategoryRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    SeedImportResult,
    SeedUrlImport,
    SourceRead,
)

SHANGHAI_TIMEZONE = "Asia/Shanghai"
_SHANGHAI = ZoneInfo(SHANGHAI_TIMEZONE)


class SourceService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._now = now or (lambda: datetime.now(UTC))

    def list_categories(self) -> list[PolicyCategoryRead]:
        with self._sessions() as session:
            return [_category_to_read(item) for item in SourceRepository(session).list_categories()]

    def list_sources(self) -> list[SourceRead]:
        with self._sessions() as session:
            return [_source_to_read(item) for item in SourceRepository(session).list_sources()]

    def list_rules(self) -> list[CollectionRuleRead]:
        with self._sessions() as session:
            return [_rule_to_read(item) for item in SourceRepository(session).list_rules()]

    def create_rule(self, payload: CollectionRuleCreate) -> CollectionRuleRead:
        try:
            with self._sessions.begin() as session:
                repository = SourceRepository(session)
                source = _require_source(repository, payload.source_code)
                category = _require_category(repository, payload.category_code)
                _validate_rule_bindings(source, category, is_active=payload.is_active)
                _validate_discovery_domains(payload.discovery, source)
                rule = CollectionRule(
                    source_id=source.id,
                    category_id=category.id,
                    name=payload.name,
                    include_keywords_json=_encode_json(payload.include_keywords),
                    exclude_keywords_json=_encode_json(payload.exclude_keywords),
                    history_years=payload.history_years,
                    discovery_config_json=_encode_json(payload.discovery.model_dump()),
                    is_active=payload.is_active,
                    source=source,
                    category=category,
                )
                repository.add_rule(rule)
                session.flush()
                result = _rule_to_read(rule)
        except IntegrityError:
            raise _conflict("RULE_CONFLICT", "采集规则与现有数据冲突。") from None
        return result

    def update_rule(
        self,
        rule_id: int,
        payload: CollectionRuleUpdate,
    ) -> CollectionRuleRead:
        if not payload.model_fields_set:
            raise _validation_error()
        try:
            with self._sessions.begin() as session:
                repository = SourceRepository(session)
                rule = repository.get_rule(rule_id)
                if rule is None:
                    raise _not_found("RULE_NOT_FOUND", "采集规则不存在。")
                values = payload.model_dump(exclude_unset=True)
                source = (
                    _require_source(repository, values["source_code"])
                    if "source_code" in values
                    else rule.source
                )
                category = (
                    _require_category(repository, values["category_code"])
                    if "category_code" in values
                    else rule.category
                )
                include_keywords = (
                    values["include_keywords"]
                    if "include_keywords" in values
                    else _decode_string_list(
                        rule.include_keywords_json,
                        allow_empty=False,
                        error_code="RULE_CONFIGURATION_INVALID",
                    )
                )
                exclude_keywords = (
                    values["exclude_keywords"]
                    if "exclude_keywords" in values
                    else _decode_string_list(
                        rule.exclude_keywords_json,
                        allow_empty=True,
                        error_code="RULE_CONFIGURATION_INVALID",
                    )
                )
                discovery = (
                    payload.discovery
                    if "discovery" in values
                    else _decode_discovery(rule.discovery_config_json)
                )
                is_active = values.get("is_active", rule.is_active)
                _validate_rule_bindings(source, category, is_active=is_active)
                _validate_discovery_domains(discovery, source)

                rule.source_id = source.id
                rule.category_id = category.id
                rule.source = source
                rule.category = category
                rule.name = values.get("name", rule.name)
                rule.include_keywords_json = _encode_json(include_keywords)
                rule.exclude_keywords_json = _encode_json(exclude_keywords)
                rule.history_years = values.get("history_years", rule.history_years)
                rule.discovery_config_json = _encode_json(discovery.model_dump())
                rule.is_active = is_active
                session.flush()
                result = _rule_to_read(rule)
        except IntegrityError:
            raise _conflict("RULE_CONFLICT", "采集规则与现有数据冲突。") from None
        return result

    def list_schedules(self) -> list[ScheduleRead]:
        with self._sessions() as session:
            return [
                _schedule_to_read(schedule, rule_name)
                for schedule, rule_name in SourceRepository(session).list_schedules()
            ]

    def create_schedule(self, payload: ScheduleCreate) -> ScheduleRead:
        cron_expression, trigger = _parse_cron(payload.cron_expression)
        del trigger
        try:
            with self._sessions.begin() as session:
                repository = SourceRepository(session)
                rule = repository.get_rule(payload.rule_id)
                if rule is None:
                    raise _not_found("RULE_NOT_FOUND", "采集规则不存在。")
                schedule = Schedule(
                    rule_id=rule.id,
                    cron_expression=cron_expression,
                    timezone=SHANGHAI_TIMEZONE,
                    is_active=False,
                    next_run_at=None,
                    last_run_at=None,
                )
                repository.add_schedule(schedule)
                session.flush()
                result = _schedule_to_read(schedule, rule.name)
        except IntegrityError:
            raise _conflict("SCHEDULE_CONFLICT", "定时计划与现有数据冲突。") from None
        return result

    def update_schedule(
        self,
        schedule_id: int,
        payload: ScheduleUpdate,
    ) -> ScheduleRead:
        if not payload.model_fields_set:
            raise _validation_error()
        try:
            with self._sessions.begin() as session:
                repository = SourceRepository(session)
                stored = repository.get_schedule(schedule_id)
                if stored is None:
                    raise _not_found("SCHEDULE_NOT_FOUND", "定时计划不存在。")
                schedule, rule_name = stored
                values = payload.model_dump(exclude_unset=True)
                expression = values.get("cron_expression", schedule.cron_expression)
                cron_expression, trigger = _parse_cron(expression)
                is_active = values.get("is_active", schedule.is_active)
                schedule.cron_expression = cron_expression
                schedule.timezone = SHANGHAI_TIMEZONE
                schedule.is_active = is_active
                schedule.next_run_at = _next_run(trigger, self._now()) if is_active else None
                session.flush()
                result = _schedule_to_read(schedule, rule_name)
        except IntegrityError:
            raise _conflict("SCHEDULE_CONFLICT", "定时计划与现有数据冲突。") from None
        return result

    def import_seed_urls(
        self,
        rule_id: int,
        entries: Iterable[SeedUrlImport],
    ) -> SeedImportResult:
        candidates = list({entry.url: entry for entry in entries}.values())
        try:
            with self._sessions.begin() as session:
                repository = SourceRepository(session)
                rule = repository.get_rule(rule_id)
                if rule is None:
                    raise _not_found("RULE_NOT_FOUND", "采集规则不存在。")
                for entry in candidates:
                    _validate_url_domain(entry.url, _allowed_domains(rule.source))
                existing_urls = repository.existing_seed_urls(rule_id, (entry.url for entry in candidates))
                for entry in candidates:
                    if entry.url in existing_urls:
                        continue
                    repository.add_seed(
                        SeedUrl(
                            rule_id=rule_id,
                            url=entry.url,
                            expected_title=entry.expected_title,
                            expected_published_date=entry.expected_published_date,
                            is_verified=entry.is_verified,
                        )
                    )
                session.flush()
        except IntegrityError:
            raise _conflict("SEED_IMPORT_CONFLICT", "种子清单正在被更新，请重试。") from None
        return SeedImportResult(
            inserted=len(candidates) - len(existing_urls),
            existing=len(existing_urls),
        )


def _category_to_read(category: PolicyCategory) -> PolicyCategoryRead:
    return PolicyCategoryRead(
        id=category.id,
        code=category.code,
        name=category.name,
        description=category.description,
        is_active=category.is_active,
    )


def _source_to_read(source: Source) -> SourceRead:
    return SourceRead(
        id=source.id,
        code=source.code,
        name=source.name,
        organization=source.organization,
        base_url=source.base_url,
        adapter_type=source.adapter_type,
        allowed_domains=_allowed_domains(source),
        is_active=source.is_active,
    )


def _rule_to_read(rule: CollectionRule) -> CollectionRuleRead:
    return CollectionRuleRead(
        id=rule.id,
        name=rule.name,
        source=_source_to_read(rule.source),
        category=_category_to_read(rule.category),
        include_keywords=_decode_string_list(
            rule.include_keywords_json,
            allow_empty=False,
            error_code="RULE_CONFIGURATION_INVALID",
        ),
        exclude_keywords=_decode_string_list(
            rule.exclude_keywords_json,
            allow_empty=True,
            error_code="RULE_CONFIGURATION_INVALID",
        ),
        history_years=rule.history_years,
        discovery=_decode_discovery(rule.discovery_config_json),
        is_active=rule.is_active,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _schedule_to_read(schedule: Schedule, rule_name: str) -> ScheduleRead:
    return ScheduleRead(
        id=schedule.id,
        rule_id=schedule.rule_id,
        rule_name=rule_name,
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        is_active=schedule.is_active,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
    )


def _require_source(repository: SourceRepository, code: str) -> Source:
    source = repository.get_source_by_code(code)
    if source is None:
        raise _not_found("SOURCE_NOT_FOUND", "来源不存在。")
    return source


def _require_category(repository: SourceRepository, code: str) -> PolicyCategory:
    category = repository.get_category_by_code(code)
    if category is None:
        raise _not_found("CATEGORY_NOT_FOUND", "政策类别不存在。")
    return category


def _validate_rule_bindings(
    source: Source,
    category: PolicyCategory,
    *,
    is_active: bool,
) -> None:
    if not is_active:
        return
    if not source.is_active:
        raise APIError(status_code=422, code="SOURCE_INACTIVE", message="启用规则需要启用的来源。")
    if not category.is_active:
        raise APIError(
            status_code=422,
            code="CATEGORY_INACTIVE",
            message="启用规则需要启用的政策类别。",
        )


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_string_list(raw: str, *, allow_empty: bool, error_code: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise _configuration_error(error_code) from None
    if not isinstance(value, list) or (not allow_empty and not value):
        raise _configuration_error(error_code)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _configuration_error(error_code)
    return value


def _decode_discovery(raw: str) -> DiscoveryConfig:
    try:
        value = json.loads(raw)
        return DiscoveryConfig.model_validate(value)
    except (TypeError, json.JSONDecodeError, ValidationError):
        raise _configuration_error("RULE_CONFIGURATION_INVALID") from None


def _allowed_domains(source: Source) -> list[str]:
    raw_domains = _decode_string_list(
        source.allowed_domains_json,
        allow_empty=False,
        error_code="SOURCE_CONFIGURATION_INVALID",
    )
    try:
        return list(dict.fromkeys(_normalize_domain(domain) for domain in raw_domains))
    except (UnicodeError, ValueError):
        raise _configuration_error("SOURCE_CONFIGURATION_INVALID") from None


def _validate_discovery_domains(discovery: DiscoveryConfig, source: Source) -> None:
    allowed_domains = _allowed_domains(source)
    for url in [*discovery.rss_urls, *discovery.channel_urls]:
        _validate_url_domain(url, allowed_domains)


def _validate_url_domain(url: str, allowed_domains: Iterable[str]) -> None:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        raise _url_not_allowed() from None
    del port
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _url_not_allowed()
    try:
        normalized_host = _normalize_domain(hostname)
    except (UnicodeError, ValueError):
        raise _url_not_allowed() from None
    if not any(
        normalized_host == allowed or normalized_host.endswith(f".{allowed}") for allowed in allowed_domains
    ):
        raise _url_not_allowed()


def _normalize_domain(value: str) -> str:
    domain = value.strip().rstrip(".")
    if (
        not domain
        or any(character in domain for character in "/:@?#")
        or any(character.isspace() for character in domain)
    ):
        raise ValueError("invalid domain")
    return domain.encode("idna").decode("ascii").lower()


def _parse_cron(expression: str) -> tuple[str, CronTrigger]:
    normalized = " ".join(expression.split())
    if len(normalized.split(" ")) != 5:
        raise APIError(status_code=422, code="INVALID_CRON", message="Cron 表达式必须为标准 5 段。")
    try:
        trigger = CronTrigger.from_crontab(normalized, timezone=_SHANGHAI)
    except ValueError:
        raise APIError(status_code=422, code="INVALID_CRON", message="Cron 表达式无效。") from None
    return normalized, trigger


def _next_run(trigger: CronTrigger, now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("时间源必须返回 aware datetime")
    next_run = trigger.get_next_fire_time(None, now.astimezone(_SHANGHAI))
    if next_run is None:
        raise APIError(status_code=422, code="INVALID_CRON", message="Cron 表达式没有下一次运行时间。")
    return next_run.astimezone(UTC)


def _not_found(code: str, message: str) -> APIError:
    return APIError(status_code=404, code=code, message=message)


def _configuration_error(code: str) -> APIError:
    return APIError(status_code=422, code=code, message="来源或规则配置无效。")


def _url_not_allowed() -> APIError:
    return APIError(status_code=422, code="URL_NOT_ALLOWED", message="URL 不在来源允许范围内。")


def _validation_error() -> APIError:
    return APIError(status_code=422, code="VALIDATION_ERROR", message="请求参数无效。")


def _conflict(code: str, message: str) -> APIError:
    return APIError(status_code=409, code=code, message=message)
