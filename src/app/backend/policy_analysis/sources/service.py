"""Business rules and short transaction boundaries for collection sources."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from policy_analysis.core.errors import APIError
from policy_analysis.sources.models import CollectionRule, PolicyCategory, SeedUrl, Source
from policy_analysis.sources.repository import SourceRepository
from policy_analysis.sources.schemas import (
    CollectionRuleCreate,
    CollectionRuleRead,
    CollectionRuleUpdate,
    DiscoveryConfig,
    PolicyCategoryRead,
    SeedImportResult,
    SeedUrlImport,
    SourceRead,
)
from policy_analysis.sources.url_validation import normalize_dns_name, normalized_http_hostname

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
                cron_expression, next_run_at = _resolve_trigger_config(
                    trigger_mode=payload.trigger_mode,
                    cron_expression=payload.cron_expression,
                    schedule_enabled=payload.schedule_enabled,
                    is_active=payload.is_active,
                    now=self._now(),
                )
                rule = CollectionRule(
                    source_id=source.id,
                    category_id=category.id,
                    name=payload.name,
                    include_keywords_json=_encode_json(payload.include_keywords),
                    exclude_keywords_json=_encode_json(payload.exclude_keywords),
                    history_years=payload.history_years,
                    discovery_config_json=_encode_json(payload.discovery.model_dump()),
                    is_active=payload.is_active,
                    trigger_mode=payload.trigger_mode,
                    cron_expression=cron_expression,
                    schedule_timezone=SHANGHAI_TIMEZONE,
                    schedule_enabled=payload.schedule_enabled,
                    next_run_at=next_run_at,
                    last_run_at=None,
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
                    values["discovery"]
                    if "discovery" in values
                    else _decode_discovery(rule.discovery_config_json)
                )
                try:
                    merged = CollectionRuleCreate.model_validate(
                        {
                            "name": values.get("name", rule.name),
                            "source_code": values.get("source_code", rule.source.code),
                            "category_code": values.get("category_code", rule.category.code),
                            "include_keywords": include_keywords,
                            "exclude_keywords": exclude_keywords,
                            "history_years": values.get("history_years", rule.history_years),
                            "discovery": discovery,
                            "is_active": values.get("is_active", rule.is_active),
                        }
                    )
                except ValidationError:
                    raise _configuration_error("RULE_CONFIGURATION_INVALID") from None
                source = _require_source(repository, merged.source_code)
                category = _require_category(repository, merged.category_code)
                _validate_rule_bindings(source, category, is_active=merged.is_active)
                _validate_discovery_domains(merged.discovery, source)

                trigger_mode = values.get("trigger_mode", rule.trigger_mode)
                if trigger_mode == "manual":
                    # 切换回手工触发即清空定时配置；请求不得再携带 cron 或启用标记。
                    if values.get("cron_expression") is not None or values.get("schedule_enabled", False):
                        raise _trigger_error("手工触发规则不能配置 Cron 或启用定时。")
                    effective_cron: str | None = None
                    effective_enabled = False
                else:
                    effective_cron = values.get("cron_expression", rule.cron_expression)
                    effective_enabled = values.get("schedule_enabled", rule.schedule_enabled)
                cron_expression, next_run_at = _resolve_trigger_config(
                    trigger_mode=trigger_mode,
                    cron_expression=effective_cron,
                    schedule_enabled=effective_enabled,
                    is_active=merged.is_active,
                    now=self._now(),
                )

                rule.source_id = source.id
                rule.category_id = category.id
                rule.source = source
                rule.category = category
                rule.name = merged.name
                rule.include_keywords_json = _encode_json(merged.include_keywords)
                rule.exclude_keywords_json = _encode_json(merged.exclude_keywords)
                rule.history_years = merged.history_years
                rule.discovery_config_json = _encode_json(merged.discovery.model_dump())
                rule.is_active = merged.is_active
                rule.trigger_mode = trigger_mode
                rule.cron_expression = cron_expression
                rule.schedule_timezone = SHANGHAI_TIMEZONE
                rule.schedule_enabled = effective_enabled
                rule.next_run_at = next_run_at
                session.flush()
                result = _rule_to_read(rule)
        except IntegrityError:
            raise _conflict("RULE_CONFLICT", "采集规则与现有数据冲突。") from None
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
        trigger_mode=rule.trigger_mode,
        cron_expression=rule.cron_expression,
        schedule_timezone=rule.schedule_timezone,
        schedule_enabled=rule.schedule_enabled,
        next_run_at=rule.next_run_at,
        last_run_at=rule.last_run_at,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
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
        normalized_host = normalized_http_hostname(url)
    except ValueError:
        raise _url_not_allowed() from None
    if not any(
        normalized_host == allowed or normalized_host.endswith(f".{allowed}") for allowed in allowed_domains
    ):
        raise _url_not_allowed()


def _normalize_domain(value: str) -> str:
    return normalize_dns_name(value)


def _parse_cron(expression: str) -> tuple[str, CronTrigger]:
    normalized = " ".join(expression.split())
    if len(normalized.split(" ")) != 5:
        raise APIError(status_code=422, code="INVALID_CRON", message="Cron 表达式必须为标准 5 段。")
    try:
        trigger = CronTrigger.from_crontab(normalized, timezone=_SHANGHAI)
    except ValueError:
        raise APIError(status_code=422, code="INVALID_CRON", message="Cron 表达式无效。") from None
    return normalized, trigger


def _resolve_trigger_config(
    *,
    trigger_mode: str,
    cron_expression: str | None,
    schedule_enabled: bool,
    is_active: bool,
    now: datetime,
) -> tuple[str | None, datetime | None]:
    """Validate trigger fields and derive (cron_expression, next_run_at)."""

    if trigger_mode == "manual":
        if cron_expression is not None or schedule_enabled:
            raise _trigger_error("手工触发规则不能配置 Cron 或启用定时。")
        return None, None
    if not cron_expression:
        raise _trigger_error("定时运行规则必须配置 Cron 表达式。")
    cron, trigger = _parse_cron(cron_expression)
    if schedule_enabled and not is_active:
        raise _trigger_error("启用定时的规则必须处于启用状态，请先启用规则。")
    next_run_at = _next_run(trigger, now) if schedule_enabled else None
    return cron, next_run_at


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


def _trigger_error(message: str) -> APIError:
    return APIError(status_code=422, code="RULE_TRIGGER_INVALID", message=message)


def _conflict(code: str, message: str) -> APIError:
    return APIError(status_code=409, code=code, message=message)
