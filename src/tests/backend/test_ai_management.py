from datetime import UTC, datetime, timedelta

from policy_analysis.ai.models import AuditEvent, Credential
from policy_analysis.auth.models import PagePermission, User
from sqlalchemy import select


def payload(scopes):
    return {
        "name": "网页验收",
        "scopes": scopes,
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }


def test_management_info_and_paginated_metadata(admin_client):
    info = admin_client.get("/api/v1/ai-access")
    assert info.status_code == 200
    assert info.json()["mcp_url"].endswith("/mcp/")
    assert len(info.json()["tools"]) == 21
    assert {"code", "label", "description"} <= info.json()["scopes"][0].keys()
    created = admin_client.post(
        "/api/v1/credentials", headers=admin_client.csrf_headers, json=payload(["policies:read"])
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    page = admin_client.get("/api/v1/ai-access/credentials?page=1&page_size=1")
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["status"] == "active"
    assert "secret" not in page.text and created.json()["secret"] not in page.text
    legacy = admin_client.get("/api/v1/credentials")
    assert created.json()["secret"] not in legacy.text
    assert legacy.headers["cache-control"] == "no-store"
    assert admin_client.get("/api/v1/policies", headers={"Authorization": "Bearer "}).status_code == 401


def test_ordinary_user_cannot_forge_scopes_or_revoke_others(admin_client, user_client, database_sessions):
    with database_sessions.begin() as db:
        reader = db.scalar(select(User).where(User.username == "reader"))
        db.add(PagePermission(user_id=reader.id, page_code="policies"))
    info = user_client.get("/api/v1/ai-access").json()
    assert [scope["code"] for scope in info["scopes"]] == ["policies:read"]
    forged = user_client.post(
        "/api/v1/credentials", headers=user_client.csrf_headers, json=payload(["rules:write"])
    )
    assert forged.status_code == 403
    missing_csrf = user_client.post("/api/v1/credentials", json=payload(["policies:read"]))
    assert missing_csrf.status_code == 403
    own = user_client.post(
        "/api/v1/credentials", headers=user_client.csrf_headers, json=payload(["policies:read"])
    )
    assert own.status_code == 201
    impersonation = user_client.post(
        "/api/v1/credentials",
        headers=user_client.csrf_headers,
        json={**payload(["policies:read"]), "user_id": 1},
    )
    assert impersonation.status_code == 422
    other = admin_client.delete("/api/v1/credentials/" + own.json()["id"], headers=admin_client.csrf_headers)
    assert other.status_code == 404
    assert admin_client.get("/api/v1/ai-access/credentials").json()["total"] == 0
    with database_sessions() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.status_code == 403)))
        assert len(events) >= 2
        assert any(event.user_id == reader.id for event in events)


def test_disabled_user_and_permission_reduction_take_effect(user_client, database_sessions):
    with database_sessions.begin() as db:
        user = db.scalar(select(User).where(User.username == "reader"))
        db.add(PagePermission(user_id=user.id, page_code="policies"))
    issued = user_client.post(
        "/api/v1/credentials", headers=user_client.csrf_headers, json=payload(["policies:read"])
    ).json()
    headers = {"Authorization": "Bearer " + issued["secret"]}
    assert user_client.get("/api/v1/policies", headers=headers).status_code == 200
    with database_sessions.begin() as db:
        db.delete(db.get(PagePermission, (user.id, "policies")))
    assert user_client.get("/api/v1/policies", headers=headers).status_code == 403
    assert user_client.get("/api/v1/ai-access").json()["scopes"] == []
    assert user_client.get("/api/v1/ai-access/credentials").json()["items"][0]["effective_scopes"] == []
    with database_sessions.begin() as db:
        db.get(User, user.id).is_active = False
    assert user_client.get("/api/v1/policies", headers=headers).status_code == 401


def test_pagination_validation_and_expired_status(admin_client, database_sessions):
    first = admin_client.post(
        "/api/v1/credentials", headers=admin_client.csrf_headers, json=payload(["policies:read"])
    ).json()
    second = admin_client.post(
        "/api/v1/credentials", headers=admin_client.csrf_headers, json=payload(["policies:read"])
    ).json()
    with database_sessions.begin() as db:
        db.get(Credential, second["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    page = admin_client.get("/api/v1/ai-access/credentials?page=1&page_size=1").json()
    assert page["total"] == 2 and page["items"][0]["status"] == "expired"
    page2 = admin_client.get("/api/v1/ai-access/credentials?page=2&page_size=1").json()
    assert page2["items"][0]["id"] == first["id"]
    assert admin_client.get("/api/v1/ai-access/credentials?page_size=1000").status_code == 422


def test_failed_revoke_audit_does_not_log_arbitrary_secret_path(admin_client, database_sessions):
    misplaced = "pa_not-a-valid-id-sensitive-example"
    response = admin_client.delete("/api/v1/credentials/" + misplaced, headers=admin_client.csrf_headers)
    assert response.status_code == 404
    with database_sessions() as db:
        events = list(db.scalars(select(AuditEvent)))
        assert events and all(misplaced not in event.target for event in events)
