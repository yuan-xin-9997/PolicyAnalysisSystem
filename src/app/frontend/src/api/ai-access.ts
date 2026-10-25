export interface ScopeOption { code: string; label: string; description: string; read_only: boolean }
export interface AccessInfo {
  api_url: string; openapi_url: string; docs_url: string; mcp_url: string; token_url: string; issuer: string
  scopes: ScopeOption[]
  tools: { name: string; description: string; annotations: { readOnlyHint?: boolean; destructiveHint?: boolean; idempotentHint?: boolean } }[]
  clients: { name: string; status: string; description: string }[]
}
export interface Credential {
  id: string; name: string; scopes: string[]; effective_scopes: string[]
  created_at: string; expires_at: string; revoked_at: string | null; status: string
}
export interface CredentialPage { items: Credential[]; total: number; page: number; page_size: number }
export interface IssuedCredential { id: string; name: string; secret: string }

export function clientExample(info: AccessInfo): string {
  return `# pip install mcp==1.30.0 httpx
# 将 POLICY_API_BASE 设置为 ${info.issuer}
# POLICY_CLIENT_ID 与 POLICY_CLIENT_SECRET 从受保护的环境注入；不写入源码。
import asyncio, os, httpx
from mcp import ClientSession
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client

class MemoryStorage:
    tokens = None
    client = None
    async def get_tokens(self): return self.tokens
    async def set_tokens(self, value): self.tokens = value
    async def get_client_info(self): return self.client
    async def set_client_info(self, value): self.client = value

async def main():
    base = os.environ["POLICY_API_BASE"].rstrip("/")
    auth = ClientCredentialsOAuthProvider(
        server_url=base + "/mcp/", issuer=base, storage=MemoryStorage(),
        client_id=os.environ["POLICY_CLIENT_ID"],
        client_secret=os.environ["POLICY_CLIENT_SECRET"],
        token_endpoint_auth_method="client_secret_basic",
    )
    async with httpx.AsyncClient(auth=auth) as http:
        async with streamable_http_client(base + "/mcp/", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("search_policies", {"arguments": {"page_size": 1}})
                # 检查 result.isError；HTTP 200 不等于业务成功。不要打印凭据或令牌。
asyncio.run(main())`
}
