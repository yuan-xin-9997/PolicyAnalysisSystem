import json
from pathlib import Path

from fastapi.testclient import TestClient
from policy_analysis.core.settings import load_settings


def _csrf(client: TestClient) -> dict[str, str]:
    return client.csrf_headers  # type: ignore[attr-defined, no-any-return]


def test_effective_settings_are_masked_and_report_each_leaf_source(
    admin_client: TestClient,
    auth_app,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "app.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"port": 31000},
                "webfetch": {"base_url": "https://fetch.example.test"},
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "POLICY_ANALYSIS_WEBFETCH__API_KEY": "webfetch-key-must-not-leak",
        "POLICY_ANALYSIS_AUTH__SESSION_SECRET": "session-secret-must-not-leak",
        "POLICY_ANALYSIS_TASKS__MAX_WORKERS": "4",
    }
    auth_app.state.settings = load_settings(config_path, tmp_path, environment)
    auth_app.state.settings_config_path = config_path
    auth_app.state.settings_environment = environment

    response = admin_client.get("/api/v1/settings/effective", headers=_csrf(admin_client))

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "webfetch-key-must-not-leak" not in serialized
    assert "session-secret-must-not-leak" not in serialized
    assert payload["values"]["webfetch"]["api_key"] == "********"
    assert payload["values"]["auth"]["session_secret"] == "********"
    assert payload["sources"]["server.host"] == "default"
    assert payload["sources"]["server.port"] == "config_file"
    assert payload["sources"]["tasks.max_workers"] == "environment"
    assert set(payload["sources"].values()) <= {"default", "config_file", "environment"}
    assert _leaf_paths(payload["values"]) == set(payload["sources"])
    assert payload["webfetch"] == {"status": "configured", "checked": False}


def test_settings_endpoint_requires_admin_and_csrf(
    admin_client: TestClient,
    user_client: TestClient,
) -> None:
    assert admin_client.get("/api/v1/settings/effective").status_code == 403
    assert user_client.get("/api/v1/settings/effective", headers=_csrf(user_client)).status_code == 403


def test_webfetch_status_is_explicitly_not_configured_without_network_probe(
    admin_client: TestClient,
    auth_app,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "app.json"
    config_path.write_text("{}", encoding="utf-8")
    auth_app.state.settings = load_settings(config_path, tmp_path, {})
    auth_app.state.settings_config_path = config_path
    auth_app.state.settings_environment = {}

    response = admin_client.get("/api/v1/settings/effective", headers=_csrf(admin_client))

    assert response.status_code == 200
    assert response.json()["webfetch"] == {"status": "not_configured", "checked": False}


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    result: set[str] = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        result.update(_leaf_paths(child, child_prefix))
    return result
