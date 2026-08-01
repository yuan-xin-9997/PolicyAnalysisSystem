import json
from pathlib import Path

import pytest
from policy_analysis.core.paths import resolve_project_path
from policy_analysis.core.settings import (
    AppSettings,
    _parse_environment_value,
    load_settings,
    load_settings_snapshot,
    masked_settings,
    settings_sources,
)
from pydantic import ValidationError


def test_environment_overrides_json_and_resolves_project_path(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text(
        json.dumps({"server": {"port": 30080}, "database": {"path": "src/data/test.sqlite3"}}),
        encoding="utf-8",
    )
    settings = load_settings(
        config_path=config,
        project_root=tmp_path,
        environ={"POLICY_ANALYSIS_SERVER__PORT": "30123"},
    )
    assert settings.server.port == 30123
    assert settings.database.path == tmp_path / "src/data/test.sqlite3"
    sources = settings_sources(config, {"POLICY_ANALYSIS_SERVER__PORT": "30123"})
    assert sources["server.port"] == "environment"
    assert sources["database.path"] == "config_file"


def test_masked_settings_never_returns_secret_values(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")
    settings = load_settings(
        config_path=config,
        project_root=tmp_path,
        environ={
            "POLICY_ANALYSIS_WEBFETCH__API_KEY": "secret-webfetch-key",
            "POLICY_ANALYSIS_AUTH__SESSION_SECRET": "secret-session-key",
        },
    )
    visible = masked_settings(settings)
    serialized = json.dumps(visible, ensure_ascii=False)
    assert "secret-webfetch-key" not in serialized
    assert "secret-session-key" not in serialized
    assert visible["webfetch"]["api_key"] == "********"


def test_invalid_server_port_is_rejected(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text(json.dumps({"server": {"port": 65536}}), encoding="utf-8")

    with pytest.raises(ValidationError, match="less than or equal to 65535"):
        load_settings(config_path=config, project_root=tmp_path, environ={})


def test_unknown_configuration_field_is_rejected(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text(json.dumps({"server": {"unsupported": True}}), encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_settings(config_path=config, project_root=tmp_path, environ={})


def test_relative_password_file_resolves_from_project_root(tmp_path) -> None:
    config = tmp_path / "app.json"
    config.write_text(json.dumps({"auth": {"password_file": "config/password.txt"}}), encoding="utf-8")

    settings = load_settings(config_path=config, project_root=tmp_path, environ={})

    assert settings.auth.password_file == tmp_path / "config/password.txt"


def test_resolve_project_path_handles_relative_and_absolute_paths(tmp_path) -> None:
    relative = resolve_project_path(tmp_path, Path("data/app.sqlite3"))
    absolute = tmp_path / "data/app.sqlite3"

    assert relative == absolute
    assert resolve_project_path(tmp_path, absolute) == absolute


def test_parse_environment_value_decodes_json_and_preserves_plain_text() -> None:
    assert _parse_environment_value("3") == 3
    assert _parse_environment_value("plain-text") == "plain-text"


def test_login_active_key_capacity_has_safe_default_and_bounds(tmp_path: Path) -> None:
    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")

    settings = load_settings(config_path=config, project_root=tmp_path, environ={})

    assert settings.auth.login_max_active_keys == 4096
    for invalid_capacity in (0, 100_001):
        config.write_text(
            json.dumps({"auth": {"login_max_active_keys": invalid_capacity}}),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_settings(config_path=config, project_root=tmp_path, environ={})


def test_build_metadata_environment_does_not_pollute_application_settings_sources(tmp_path: Path) -> None:
    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")
    environment = {
        "POLICY_ANALYSIS_VERSION": "v0.123",
        "POLICY_ANALYSIS_COMMIT_SHA": "abcdef1",
    }

    settings = load_settings(config_path=config, project_root=tmp_path, environ=environment)
    sources = settings_sources(config, environment)

    assert settings.server.port == 30080
    assert "version" not in sources
    assert "commit_sha" not in sources


def test_top_level_json_environment_object_marks_each_effective_leaf_as_environment(
    tmp_path: Path,
) -> None:
    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")
    environment = {
        "POLICY_ANALYSIS_SERVER": '{"host":"0.0.0.0","port":31001}',
    }

    settings = load_settings(config, tmp_path, environment)
    sources = settings_sources(config, environment)

    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 31001
    assert sources["server.host"] == "environment"
    assert sources["server.port"] == "environment"
    assert "server" not in sources


def test_top_level_environment_object_replaces_config_source_subtree(
    tmp_path: Path,
) -> None:
    config = tmp_path / "app.json"
    config.write_text('{"server":{"host":"config-host"}}', encoding="utf-8")

    snapshot = load_settings_snapshot(
        config,
        tmp_path,
        {"POLICY_ANALYSIS_SERVER": '{"port":31001}'},
    )

    assert snapshot.settings.server.host == "127.0.0.1"
    assert snapshot.settings.server.port == 31001
    assert snapshot.sources["server.host"] == "default"
    assert snapshot.sources["server.port"] == "environment"
    assert set(snapshot.sources) == _leaf_paths(masked_settings(snapshot.settings))


def test_nested_environment_leaf_overrides_replaced_top_level_object_source(
    tmp_path: Path,
) -> None:
    config = tmp_path / "app.json"
    config.write_text('{"server":{"host":"config-host"}}', encoding="utf-8")

    snapshot = load_settings_snapshot(
        config,
        tmp_path,
        {
            "POLICY_ANALYSIS_SERVER": '{"port":31001}',
            "POLICY_ANALYSIS_SERVER__HOST": "nested-environment-host",
        },
    )

    assert snapshot.settings.server.host == "nested-environment-host"
    assert snapshot.settings.server.port == 31001
    assert snapshot.sources["server.host"] == "environment"
    assert snapshot.sources["server.port"] == "environment"
    assert set(snapshot.sources) == _leaf_paths(masked_settings(snapshot.settings))


def test_pagination_settings_have_validated_defaults_and_bounds() -> None:
    settings = AppSettings()

    assert settings.pagination.default_page_size == 50
    assert settings.pagination.max_page_size == 100
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"pagination": {"default_page_size": 20, "max_page_size": 10}})


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    result: set[str] = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        result.update(_leaf_paths(child, child_prefix))
    return result
