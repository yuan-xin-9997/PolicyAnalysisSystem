import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

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


class AppSettings(StrictSettingsModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    webfetch: WebFetchSettings = Field(default_factory=WebFetchSettings)
    tasks: TaskSettings = Field(default_factory=TaskSettings)


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
    data = json.loads(json.dumps(configured))
    for name, raw_value in sorted(environ.items(), key=lambda item: item[0].count("__")):
        if not name.startswith("POLICY_ANALYSIS_") or name in _BUILD_METADATA_NAMES:
            continue
        keys = [part.lower() for part in name.removeprefix("POLICY_ANALYSIS_").split("__")]
        _set_nested(data, keys, _parse_environment_value(raw_value))
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
    return SettingsSnapshot(resolved, _settings_sources(configured, environ))


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
    defaults = AppSettings().model_dump(mode="json")

    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if not isinstance(value, dict):
            return {prefix: value}
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(flatten(child, child_prefix))
        return flattened

    sources = {path: "default" for path in flatten(defaults)}
    sources.update({path: "config_file" for path in flatten(configured)})
    for name, raw_value in sorted(environ.items(), key=lambda item: item[0].count("__")):
        if name.startswith("POLICY_ANALYSIS_") and name not in _BUILD_METADATA_NAMES:
            path = name.removeprefix("POLICY_ANALYSIS_").lower().replace("__", ".")
            parsed = _parse_environment_value(raw_value)
            if isinstance(parsed, dict):
                sources.update({leaf: "environment" for leaf in flatten(parsed, path)})
                sources.pop(path, None)
            else:
                sources[path] = "environment"
    return sources
