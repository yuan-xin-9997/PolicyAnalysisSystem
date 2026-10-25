from datetime import UTC, datetime, timedelta

from sqlalchemy import select


def issue(client, scopes):
    response = client.post(
        "/api/v1/credentials",
        headers=client.csrf_headers,
        json={
            "name": "integration",
            "scopes": scopes,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_machine_auth_scope_revocation(admin_client):
    credential = issue(admin_client, ["policies:read"])
    headers = {"Authorization": "Bearer " + credential["secret"]}
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 200
    assert admin_client.get("/api/v1/tasks", headers=headers).status_code == 403
    assert admin_client.get("/api/v1/policies", headers={"Authorization": "Bearer wrong"}).status_code == 401
    revoked = admin_client.delete(
        "/api/v1/credentials/" + credential["id"], headers=admin_client.csrf_headers
    )
    assert revoked.status_code == 204
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 401


def test_idempotency_failure_and_audit(admin_client, database_sessions):
    from policy_analysis.ai.models import AuditEvent, Credential

    credential = issue(admin_client, ["analysis:write", "analysis:read"])
    headers = {"Authorization": "Bearer " + credential["secret"], "Idempotency-Key": "analysis-test"}
    # A missing policy is a business failure and must be replayed without a second operation.
    first = admin_client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999999]})
    assert first.status_code == 404
    second = admin_client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999999]})
    assert second.json() == first.json()
    conflict = admin_client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999998]})
    assert conflict.status_code == 409
    with database_sessions() as db:
        assert db.scalar(select(Credential)).secret_hash != credential["secret"]
        assert db.scalar(select(AuditEvent).where(AuditEvent.credential_id == credential["id"])) is not None


def test_oauth_resource_and_mcp_anonymous(admin_client):
    credential = issue(admin_client, ["policies:read"])
    resource = admin_client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"]
    response = admin_client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": credential["id"],
            "client_secret": credential["secret"],
            "resource": resource,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"] != credential["secret"]
    from urllib.parse import urlsplit

    mcp_headers = {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Host": urlsplit(resource).netloc,
        "Accept": "application/json, text/event-stream",
    }
    for name in ([], {}):
        invalid = admin_client.post(
            "/mcp/",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            },
        )
        assert invalid.status_code == 200, invalid.text
        assert invalid.json()["error"]["code"] == -32602
    assert admin_client.post("/mcp/", json={}).status_code == 401
    assert (
        admin_client.post(
            "/mcp/anything", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        ).status_code
        == 401
    )
    bad = admin_client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": credential["id"],
            "client_secret": credential["secret"],
            "resource": "https://other.example/mcp/",
        },
    )
    assert bad.status_code == 400


def test_oauth_streaming_body_limit(admin_client):
    response = admin_client.post(
        "/oauth/token",
        content=iter([b"client_secret=" + b"x" * 9000]),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_rule_writes_use_existing_service(admin_client, database_sessions):
    from policy_analysis.sources.bootstrap import bootstrap_default_catalog

    bootstrap_default_catalog(database_sessions)
    credential = issue(admin_client, ["rules:read", "rules:write"])
    headers = {
        "Authorization": "Bearer " + credential["secret"],
        "Idempotency-Key": "create-rule",
        "X-Confirm-Operation": "true",
    }
    sources = admin_client.get("/api/v1/sources", headers=headers).json()
    categories = admin_client.get("/api/v1/policy-categories", headers=headers).json()
    payload = {
        "name": "AI 验收手工规则",
        "source_code": sources[0]["code"],
        "category_code": categories[0]["code"],
        "include_keywords": ["发展"],
        "discovery": {"channel_urls": ["https://www.news.cn/politics/"]},
        "is_active": False,
    }
    created = admin_client.post("/api/v1/collection-rules", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    replay = admin_client.post("/api/v1/collection-rules", headers=headers, json=payload)
    assert replay.json()["id"] == created.json()["id"]
    headers["Idempotency-Key"] = "update-rule"
    updated = admin_client.patch(
        "/api/v1/collection-rules/" + str(created.json()["id"]), headers=headers, json={"name": "已修改规则"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "已修改规则"


def test_concurrent_duplicate_requests(admin_client, database_sessions):
    from concurrent.futures import ThreadPoolExecutor

    from policy_analysis.ai.models import IdempotentRequest

    credential = issue(admin_client, ["analysis:write"])
    headers = {"Authorization": "Bearer " + credential["secret"], "Idempotency-Key": "concurrent"}

    def send(_):
        return admin_client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999999]})

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(send, range(4)))
    assert {r.status_code for r in responses} <= {404, 409}
    assert any(r.status_code == 404 for r in responses)
    with database_sessions() as db:
        assert len(list(db.scalars(select(IdempotentRequest)))) == 1


def test_expired_and_dynamic_permissions(admin_client, database_sessions):
    from policy_analysis.ai.models import Credential
    from policy_analysis.auth.models import User

    credential = issue(admin_client, ["policies:read"])
    headers = {"Authorization": "Bearer " + credential["secret"]}
    with database_sessions.begin() as db:
        row = db.get(Credential, credential["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 401
    with database_sessions.begin() as db:
        row = db.get(Credential, credential["id"])
        row.expires_at = datetime.now(UTC) + timedelta(hours=1)
        user = db.get(User, row.user_id)
        user.role = "user"
    # The existing key cannot retain administrator rights after the user's permissions change.
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 403


def test_machine_requires_key_and_confirmation(admin_client):
    credential = issue(admin_client, ["rules:write", "analysis:write"])
    headers = {"Authorization": "Bearer " + credential["secret"]}
    response = admin_client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [1]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    response = admin_client.patch("/api/v1/collection-rules/1", headers=headers, json={"is_active": False})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OPERATION_CONFIRMATION_REQUIRED"


def test_rate_limit_and_documentation(admin_client):
    from policy_analysis.core.settings import AISettings

    credential = issue(admin_client, ["policies:read"])
    settings = admin_client.app.state.settings
    admin_client.app.state.settings = settings.model_copy(update={"ai": AISettings(requests_per_minute=1)})
    headers = {"Authorization": "Bearer " + credential["secret"]}
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 200
    assert admin_client.get("/api/v1/policies", headers=headers).status_code == 429
    schema = admin_client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/policies"]["get"]["x-required-scope"] == "policies:read"


def test_restart_keeps_idempotency(auth_app, client_context, password_file):
    password_file.write_text("admin:admin123:admin\n", encoding="utf-8")
    with client_context(auth_app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        client.csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        credential = issue(client, ["analysis:write"])
        headers = {"Authorization": "Bearer " + credential["secret"], "Idempotency-Key": "restart"}
        first = client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999999]})
        assert first.status_code == 404
    with client_context(auth_app) as client:
        second = client.post("/api/v1/analysis/tasks", headers=headers, json={"policy_ids": [999999]})
        assert second.json() == first.json()
        assert second.headers["Idempotency-Replayed"] == "true"
