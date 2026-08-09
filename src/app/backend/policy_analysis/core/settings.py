from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from policy_analysis.core.paths import resolve_project_path

_BUILD_METADATA_NAMES = {"POLICY_ANALYSIS_VERSION", "POLICY_ANALYSIS_COMMIT_SHA"}


class StrictSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerSettings(StrictSettingsModel):
    host: str = "127.0.0.1"
    port: int = Field(default=30080, ge=1, le=65535)


class DatabaseSettings(StrictSettingsModel):
    path: Path = Path("src/data/app.sqlite3")


class AuthSettings(StrictSettingsModel):
    password_file: Path = Path("src/data/password.txt")
    session_secret: SecretStr = SecretStr("")
    session_hours: int = Field(default=12, ge=1, le=168)
    secure_cookie: bool = False
    login_attempts: int = Field(default=5, ge=1, le=20)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)
    login_max_active_keys: int = Field(default=4096, ge=1, le=100_000)


class WebFetchSettings(StrictSettingsModel):
    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class TaskSettings(StrictSettingsModel):
    max_workers: int = Field(default=2, ge=1, le=8)
    retry_attempts: int = Field(default=3, ge=1, le=5)


class PaginationSettings(StrictSettingsModel):
    default_page_size: int = Field(default=50, ge=1)
    max_page_size: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_default_within_maximum(self) -> PaginationSettings:
        if self.default_page_size > self.max_page_size:
            raise ValueError("默认分页大小不能超过最大分页大小")
        return self


class AnalysisSettings(StrictSettingsModel):
    max_workers: int = Field(default=1, ge=1, le=4)
    top_words_default: int = Field(default=50, ge=1, le=500)
    min_word_length: int = Field(default=2, ge=1, le=10)
    max_policies_per_task: int = Field(default=100, ge=1, le=1000)


class AppSettings(StrictSettingsModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    webfetch: WebFetchSettings = Field(default_factory=WebFetchSettings)
    tasks: TaskSettings = Field(default_factory=TaskSettings)
    pagination: PaginationSettings = Field(default_factory=PaginationSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    settings: AppSettings
    sources: dict[str, str]


def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        child = current.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"配置路径冲突: {'__'.join(keys)}")
        current = child
    current[keys[-1]] = value


def _parse_environment_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def load_settings(
    config_path: Path,
    project_root: Path,
    environ: Mapping[str, str],
) -> AppSettings:
    return load_settings_snapshot(config_path, project_root, environ).settings


def load_settings_snapshot(
    config_path: Path,
    project_root: Path,
    environ: Mapping[str, str],
) -> SettingsSnapshot:
    configured = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    data, sources = _merged_configuration(configured, environ)
    settings = AppSettings.model_validate(data)
    resolved = settings.model_copy(
        update={
            "database": settings.database.model_copy(
                update={"path": resolve_project_path(project_root, settings.database.path)}
            ),
            "auth": settings.auth.model_copy(
                update={"password_file": resolve_project_path(project_root, settings.auth.password_file)}
            ),
        }
    )
    effective_leaves = _flatten(settings.model_dump(mode="json"))
    return SettingsSnapshot(
        resolved,
        {path: sources.get(path, "default") for path in effective_leaves},
    )


def masked_settings(settings: AppSettings) -> dict[str, object]:
    data = settings.model_dump(mode="json")
    sensitive_parts = ("password", "secret", "token", "api_key")

    def mask(value: Any, key: str = "") -> Any:
        if any(part in key.lower() for part in sensitive_parts):
            return "********"
        if isinstance(value, dict):
            return {child_key: mask(child, child_key) for child_key, child in value.items()}
        if isinstance(value, list):
            return [mask(child) for child in value]
        return value

    return mask(data)


def settings_sources(config_path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    configured = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    return _settings_sources(configured, environ)


def _settings_sources(configured: dict[str, Any], environ: Mapping[str, str]) -> dict[str, str]:
    data, sources = _merged_configuration(configured, environ)
    effective = AppSettings.model_validate(data).model_dump(mode="json")
    return {path: sources.get(path, "default") for path in _flatten(effective)}


def _merged_configuration(
    configured: dict[str, Any],
    environ: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    data = json.loads(json.dumps(configured))
    source_tree = _source_tree(AppSettings().model_dump(mode="json"), "default")
    _overlay_config_sources(source_tree, configured)
    for name, raw_value in sorted(environ.items(), key=lambda item: item[0].count("__")):
        if name.startswith("POLICY_ANALYSIS_") and name not in _BUILD_METADATA_NAMES:
            keys = [part.lower() for part in name.removeprefix("POLICY_ANALYSIS_").split("__")]
            parsed = _parse_environment_value(raw_value)
            _set_nested(data, keys, parsed)
            _set_nested(source_tree, keys, _source_tree(parsed, "environment"))
    return data, _flatten(source_tree)


def _source_tree(value: Any, source: str) -> Any:
    if not isinstance(value, dict):
        return source
    return {key: _source_tree(child, source) for key, child in value.items()}


def _overlay_config_sources(source_tree: dict[str, Any], configured: dict[str, Any]) -> None:
    for key, value in configured.items():
        if isinstance(value, dict) and isinstance(source_tree.get(key), dict):
            _overlay_config_sources(source_tree[key], value)
        else:
            source_tree[key] = _source_tree(value, "config_file")


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        flattened.update(_flatten(child, child_prefix))
    return flattened
