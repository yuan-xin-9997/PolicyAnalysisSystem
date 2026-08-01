from pathlib import Path

from fastapi import FastAPI
from policy_analysis.main import create_app


def _spa_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="spa-shell">policy-spa</div></body></html>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("globalThis.policySpa = true;", encoding="utf-8")
    (assets / "app.css").write_text("body { color: #153c32; }", encoding="utf-8")
    return dist


def _spa_app(auth_app: FastAPI, dist: Path) -> FastAPI:
    return create_app(auth_service=auth_app.state.auth_service, frontend_dist=dist)


def test_spa_serves_login_and_business_history_fallback(
    auth_app: FastAPI,
    client_context,
    tmp_path: Path,
) -> None:
    app = _spa_app(auth_app, _spa_dist(tmp_path))

    with client_context(app) as client:
        for path in ("/login", "/policies/42", "/settings"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert "policy-spa" in response.text


def test_spa_serves_real_static_files_with_safe_mime_types(
    auth_app: FastAPI,
    client_context,
    tmp_path: Path,
) -> None:
    app = _spa_app(auth_app, _spa_dist(tmp_path))

    with client_context(app) as client:
        script = client.get("/assets/app.js")
        stylesheet = client.get("/assets/app.css")

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.text == "globalThis.policySpa = true;"
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_spa_never_swallows_api_health_or_non_get_boundaries(
    auth_app: FastAPI,
    client_context,
    tmp_path: Path,
) -> None:
    app = _spa_app(auth_app, _spa_dist(tmp_path))

    with client_context(app) as client:
        api_missing = client.get("/api/v1/not-found")
        api_root = client.get("/api")
        health_missing = client.get("/health/not-found")
        health_root = client.get("/health")
        non_get = client.post("/api/v1/auth/me")

    for response in (api_missing, api_root, health_missing, health_root, non_get):
        assert response.headers["content-type"].startswith("application/json")
        assert "error" in response.json()
        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert api_missing.status_code == 404
    assert api_missing.json()["error"]["code"] == "NOT_FOUND"
    assert api_root.status_code == 404
    assert health_missing.status_code == 404
    assert health_root.status_code == 404
    assert non_get.status_code == 405


def test_spa_rejects_directory_traversal_and_missing_assets(
    auth_app: FastAPI,
    client_context,
    tmp_path: Path,
) -> None:
    dist = _spa_dist(tmp_path)
    (tmp_path / "outside-secret.txt").write_text("must-not-leak", encoding="utf-8")
    app = _spa_app(auth_app, dist)

    with client_context(app) as client:
        responses = [
            client.get("/assets/%2e%2e/outside-secret.txt"),
            client.get("/assets/..%2Foutside-secret.txt"),
            client.get("/assets/missing.js"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert "must-not-leak" not in response.text


def test_missing_frontend_dist_does_not_break_api_startup(
    auth_app: FastAPI,
    client_context,
    tmp_path: Path,
) -> None:
    app = _spa_app(auth_app, tmp_path / "missing-dist")

    with client_context(app) as client:
        live = client.get("/health/live")
        login_page = client.get("/login")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert login_page.status_code == 404
    assert login_page.headers["content-type"].startswith("application/json")
