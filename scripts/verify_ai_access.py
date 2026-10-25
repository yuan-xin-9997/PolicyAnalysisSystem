"""Real HTTP + official MCP client acceptance check; secrets only from environment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client


class MemoryStorage:
    tokens = None
    client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        self.client = client_info


async def verify(base, username, password):
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as admin:
        login = await admin.post("/api/v1/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        issued = await admin.post(
            "/api/v1/credentials",
            headers=csrf,
            json={
                "name": "acceptance-" + secrets.token_hex(4),
                "scopes": ["policies:read", "analysis:read", "analysis:write"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        issued.raise_for_status()
        credential = issued.json()
        try:
            return await exercise(base, credential)
        finally:
            revoked = await admin.delete("/api/v1/credentials/" + credential["id"], headers=csrf)
            revoked.raise_for_status()
            invalid = await admin.get(
                "/api/v1/policies", headers={"Authorization": "Bearer " + credential["secret"]}
            )
            assert invalid.status_code == 401


async def exercise(base, credential):
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as api:
        assert (await api.get("/api/v1/policies")).status_code == 401
        assert (await api.get("/api/v1/policies", headers={"Authorization": "Bearer bad"})).status_code == 401
        headers = {"Authorization": "Bearer " + credential["secret"]}
        assert (await api.get("/api/v1/tasks", headers=headers)).status_code == 403
        policies = await api.get("/api/v1/policies", headers=headers, params={"page_size": 1})
        policies.raise_for_status()
        assert policies.json()["items"], "Acceptance requires at least one existing policy"
        policy_id = policies.json()["items"][0]["id"]
        metadata = (await api.get("/.well-known/oauth-protected-resource/mcp")).json()
        resource = metadata["resource"]
        issuer = metadata["authorization_servers"][0]
        provider = ClientCredentialsOAuthProvider(
            server_url=resource,
            storage=MemoryStorage(),
            client_id=credential["id"],
            client_secret=credential["secret"],
            token_endpoint_auth_method="client_secret_basic",
            issuer=issuer,
            scopes=" ".join(credential["scopes"]),
        )
        async with httpx.AsyncClient(auth=provider, timeout=30, trust_env=False) as oauth:  # noqa: SIM117
            async with streamable_http_client(resource, http_client=oauth) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    listing = await session.list_tools()
                    names = {tool.name for tool in listing.tools}
                    assert {"search_policies", "create_analysis_task"} <= names
                    query = await session.call_tool(
                        "search_policies",
                        {
                            "arguments": {
                                "page_size": 1,
                                "published_from": "2000-01-01T00:00:00+08:00",
                            }
                        },
                    )
                    assert not query.isError, query
                    denied = await session.call_tool("list_collection_tasks", {"arguments": {}})
                    assert denied.isError
                    args = {
                        "arguments": {
                            "policy_ids": [policy_id],
                            "confirm": True,
                            "idempotency_key": "mcp-" + secrets.token_hex(8),
                        }
                    }
                    created = await session.call_tool("create_analysis_task", args)
                    assert not created.isError, created
                    replayed = await session.call_tool("create_analysis_task", args)
                    assert not replayed.isError, replayed
                    data = json.loads(created.content[0].text)
                    assert json.loads(replayed.content[0].text)["task_id"] == data["task_id"]
                    task_id = data["task_id"]
        for _ in range(200):
            task = await api.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers)
            task.raise_for_status()
            if task.json()["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.1)
        assert task.json()["status"] == "succeeded", task.text
        words = await api.get(f"/api/v1/analysis/tasks/{task_id}/words", headers=headers)
        words.raise_for_status()
        assert words.json()["items"], words.text
        return {
            "protocol": initialized.protocolVersion,
            "tools": len(names),
            "task_id": task_id,
            "task_status": task.json()["status"],
            "word_count": len(words.json()["items"]),
            "auth_negatives": "anonymous, wrong, insufficient_scope, revoked rejected",
            "oauth": "SDK discovery + client_credentials + resource binding",
            "replay": "same task_id",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from-password-file",
        action="store_true",
        help="部署服务器本机验收：读取现有受保护密码文件中的管理员，不输出凭据",
    )
    args = parser.parse_args()
    if args.from_password_file:
        from deployment import environment
        from policy_analysis.auth.password_file import parse_password_text
        from policy_analysis.core.settings import load_settings

        root = Path(__file__).resolve().parents[1]
        settings = load_settings(root / "src/config/app.json", root, environment(root))
        admins = [
            entry
            for entry in parse_password_text(settings.auth.password_file.read_text(encoding="utf-8"))
            if entry.role == "admin"
        ]
        if not admins or not settings.ai.public_base_url:
            raise RuntimeError("验收需要已配置的规范站点地址及有效管理员。")
        base, username, password = settings.ai.public_base_url, admins[0].username, admins[0].password
    else:
        base, username, password = (
            os.environ["AI_VERIFY_BASE_URL"],
            os.environ["AI_VERIFY_USERNAME"],
            os.environ["AI_VERIFY_PASSWORD"],
        )
    print(
        json.dumps(
            asyncio.run(verify(base.rstrip("/"), username, password)),
            ensure_ascii=True,
        )
    )
