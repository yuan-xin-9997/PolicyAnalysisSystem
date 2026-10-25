# AI API 与 MCP 接入

## 地址与状态

部署沿用现有主机、端口及反向代理，不增加监听端口或公开映射。以已有站点为例，部署后的地址为：

- API：`https://policy.yuan-xin.top/api/v1`
- OpenAPI：`https://policy.yuan-xin.top/openapi.json`
- 文档：`https://policy.yuan-xin.top/docs`
- MCP Streamable HTTP：`https://policy.yuan-xin.top/mcp/`（保留末尾 `/`）
- RFC 9728 资源元数据：`/.well-known/oauth-protected-resource/mcp`
- RFC 8414 授权服务器元数据：`/.well-known/oauth-authorization-server`

上述地址已通过 Jenkins 构建 40 的真实 HTTPS OAuth/MCP 语义验收。部署提交、备份位置、测试证据和明确限制见 `docs/ai-delivery-status.md`。

## 凭据与权限

现有网页登录、Cookie、CSRF 与角色/页面权限保持兼容。用户先用网页登录建立会话，通过下面接口管理自己的自动化凭据；机器凭据不能管理凭据、用户或系统配置。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/v1/credentials` | 创建凭据，要求网页登录及 `X-CSRF-Token` |
| `GET /api/v1/credentials?offset=0&limit=50` | 分页列出自己的凭据元数据 |
| `DELETE /api/v1/credentials/{id}` | 撤销自己的凭据及其所有短期令牌，要求 CSRF |
| `GET /api/v1/audit-events?offset=0&limit=50` | 管理员分页查看审计记录 |

创建示例：

```json
{
  "name": "policy-assistant",
  "scopes": ["policies:read", "analysis:read", "analysis:write"],
  "expires_at": "2026-12-01T00:00:00+08:00"
}
```

响应包括 `id`、`name`、`scopes`、创建/过期/撤销时间，以及仅返回一次的 `secret`。过期时间必须带时区，在未来且不超过 365 天。复制到客户端受保护的凭据存储或环境变量；不要写入仓库、聊天、日志或普通配置文件。服务端只保存随机 256 位凭据和短期令牌的 SHA-256 摘要。

| scope | 原权限约束 | 能力 |
| --- | --- | --- |
| `policies:read` | policies 页面 | 政策检索、筛选项、详情 |
| `rules:read` | tasks 页面 | 来源、类别、采集规则 |
| `rules:write` | 管理员 | 创建、修改采集规则 |
| `tasks:read` | tasks 页面 | 采集任务、进度、条目、日志 |
| `tasks:write` | 管理员 | 创建、取消采集任务 |
| `analysis:read` | analysis 页面 | 分析任务、日志、词频、共现和比对报告 |
| `analysis:write` | analysis 页面 | 创建分析与比对任务 |

每次调用都检查所属用户是否仍启用，并将 scope 与用户当前权限取交集；撤销管理员或页面权限立即影响旧凭据。任务可见范围沿用原页面权限规则，不额外赋予跨页面访问权限。默认没有删除数据、批量修改、发布、用户管理、配置修改工具。

## API 调用

运行时通过环境变量提供 `POLICY_API_BASE`、`POLICY_API_KEY`：

```bash
curl "$POLICY_API_BASE/api/v1/policies?page=1&page_size=20&keyword=经济" \
  -H "Authorization: Bearer $POLICY_API_KEY"

curl "$POLICY_API_BASE/api/v1/analysis/tasks" \
  -H "Authorization: Bearer $POLICY_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: analysis-job-20261025-001' \
  -d '{"policy_ids":[1,2]}'
```

分析创建响应示例：`{"task_id":42,"status":"pending"}`。轮询 `GET /api/v1/analysis/tasks/42`，终态为 `succeeded` 或 `failed`；失败原因在 `error_summary` 和 `/logs` 中。成功后读取 `/words`、`/relations`；比对任务读取 `/comparison-report`。任务创建返回 200 是历史接口约定，只表示任务已入队，不代表分析完成。采集任务创建返回 201，使用响应 `id` 查询 `/api/v1/tasks/{id}`、`/items`、`/logs`。

采集规则修改示例：

```bash
curl -X PATCH "$POLICY_API_BASE/api/v1/collection-rules/1" \
  -H "Authorization: Bearer $POLICY_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: update-rule-20261025-001' \
  -H 'X-Confirm-Operation: true' \
  -d '{"is_active":false}'
```

Bearer 写请求必须提供 `Idempotency-Key`，1–128 个字母、数字、点、下划线、冒号或连字符。规则修改还必须提供确认头。相同凭据、幂等键、方法、路径、查询参数和正文重试会返回原响应及 `Idempotency-Replayed: true`；同键不同内容返回 409。请求正文按字节比较，重试不要改变 JSON 序列化方式。

幂等记录和响应持久保存，重启后有效。同键并发仅一个请求获准执行，其余返回 409。若进程在业务提交与响应登记之间崩溃，记录保留为处理中，返回 `IDEMPOTENCY_IN_PROGRESS`；必须根据任务列表、创建人和审计核查，不能换一个键盲目重做。系统不宣称跨两个事务的严格 exactly-once。历史凭据、审计、幂等记录目前不自动清理，应按实际存储需求制定备份及留存策略。

分页接口使用 `page`/`page_size`，凭据与审计使用 `offset`/`limit`。来源、类别、规则目录沿用原接口完整列表。默认每凭据每分钟 120 次，每来源 IP 对带凭据请求每分钟 600 次；OAuth 换令牌每来源 IP 每分钟 30 次。限流是单进程固定窗口，重启清空，多进程部署需共享限流存储。本项目现有部署为单进程。MCP 外层请求与内部业务调用分别计数。写正文默认上限 256 KiB。

统一 API 错误示例：

```json
{"error":{"code":"INSUFFICIENT_SCOPE","message":"凭据无效、已过期或权限不足。","details":{},"request_id":"..."}}
```

401 表示缺失、错误、撤销、过期凭据；403 表示用户权限或 scope 不足；404 表示资源不存在；409 表示幂等冲突或状态冲突；422 表示校验/确认失败；429 附 `Retry-After: 60`；503 表示执行器或数据库不可用。OpenAPI 提供真实请求/响应模型、Bearer/Cookie 安全方案、scope、幂等头及示例。

## MCP OAuth 与客户端

使用官方 `mcp==1.30.0` Python SDK 的 Streamable HTTP 服务和 `io.modelcontextprotocol/oauth-client-credentials` 机器鉴权扩展。资源服务器通过 401 的 `WWW-Authenticate` 指向资源元数据；客户端发现授权服务器并使用预先创建的凭据（`id` 为 client_id，`secret` 为 client_secret）换取最长 15 分钟的随机令牌。

```bash
curl "$POLICY_API_BASE/oauth/token" \
  -u "$POLICY_CLIENT_ID:$POLICY_CLIENT_SECRET" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode "resource=$POLICY_API_BASE/mcp/" \
  --data-urlencode 'scope=policies:read analysis:read analysis:write'
```

也支持 `client_secret_post`（将 `client_id`、`client_secret` 放入表单）。必须校验 resource 与配置的 MCP 地址完全一致。申请已知但超出凭据授权的 scope 时，下调为交集，并在响应 `scope` 中明确实际授予范围；未知或空交集返回 `invalid_scope`。短期令牌不能作为新的 client_secret，父凭据撤销或过期后立即失效。

已实测客户端：官方 Python SDK `ClientCredentialsOAuthProvider` + `ClientSession`。最小接入代码（`MemoryStorage` 实现见 `scripts/verify_ai_access.py`）：

```python
import os
import httpx
from mcp import ClientSession
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client
from scripts.verify_ai_access import MemoryStorage

base = os.environ["POLICY_API_BASE"]
provider = ClientCredentialsOAuthProvider(
    server_url=base + "/mcp/", issuer=base,
    storage=MemoryStorage(), client_id=os.environ["POLICY_CLIENT_ID"],
    client_secret=os.environ["POLICY_CLIENT_SECRET"],
    token_endpoint_auth_method="client_secret_basic",
)
async with httpx.AsyncClient(auth=provider) as http:
    async with streamable_http_client(base + "/mcp/", http_client=http) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("search_policies", {"arguments": {"page_size": 20}})
```

支持自定义 Authorization 头的其他客户端可以预先换取短期令牌后连接，不能直接把普通 API Key 当作 MCP 令牌。未实现 authorization_code/PKCE、动态客户端注册、刷新令牌或交互授权页，因此不声明直接兼容 ChatGPT、Claude、Cursor 等客户端的交互式远程 OAuth 流程。`/oauth/authorize` 明确返回 `unsupported_response_type`，元数据仅声明 `client_credentials`。远程客户端必须通过现有 HTTPS 地址连接，HTTP 仅用于本机/受信任内网验收。

## 工具清单

所有工具使用结构化 `arguments` 参数；`tools/list` 是实际 schema 的权威来源。

| 工具 | 参数 | 属性/权限 |
| --- | --- | --- |
| `search_policies` | keyword、full_text、日期、来源、类别、page、page_size、排序 | 只读，policies:read |
| `get_policy` | policy_id | 只读，policies:read |
| `get_policy_filters` | 无 | 只读，policies:read |
| `list_sources`、`list_categories`、`list_collection_rules` | 无 | 只读，rules:read |
| `create_collection_rule` | payload、confirm、idempotency_key | 管理员、rules:write、可能触发外部定时采集 |
| `update_collection_rule` | rule_id、payload、confirm、idempotency_key | 管理员、rules:write、破坏性修改 |
| `create_collection_task` | rule_id、confirm、idempotency_key | 管理员、tasks:write、访问外部来源 |
| `list_collection_tasks` | page、page_size | 只读，tasks:read |
| `get_collection_task` | task_id | 只读，tasks:read |
| `list_collection_items`、`list_collection_logs` | task_id、page、page_size | 只读，tasks:read |
| `create_analysis_task` | policy_ids、confirm、idempotency_key | analysis:write |
| `create_comparison_task` | policy_ids（至少两篇不同政策）、confirm、idempotency_key | analysis:write |
| `list_analysis_tasks` | page、page_size | 只读，analysis:read |
| `get_analysis_task` | task_id | 只读，analysis:read |
| `list_analysis_logs` | task_id、page、page_size | 只读，analysis:read |
| `get_analysis_words` | task_id、top、sort_by | 只读，analysis:read |
| `get_analysis_relations` | task_id、top | 只读，analysis:read |
| `get_comparison_report` | task_id | 只读，analysis:read |

工具写入必须传 `confirm:true` 及幂等键。客户端应先向业务用户确认修改内容，不能把工具属性当作权限控制。只读工具标记 readOnly；写工具依据规则/采集副作用声明 destructive/openWorld；写工具通过必填幂等键声明 idempotent。业务错误返回 `isError:true`，不能因 MCP HTTP 200 就判断业务成功。

## 安全边界和公开入口

匿名只能访问静态页面/静态资源、`/health/live`、`/health/ready`、`/docs`、`/redoc`、`/openapi.json`、两类 OAuth 元数据及登录/令牌校验入口；这些入口不返回政策、任务或凭据数据。登录与换令牌仍要求有效凭据。MCP 的 initialize、tools/list、tools/call 均要求短期 OAuth 令牌，携带 Cookie 也不会绕过认证。MCP 校验 Host 为配置地址，拒绝浏览器 Origin，凭据只从 Authorization 头读取。

业务写入通过已有 API、依赖和业务服务，MCP 不直接修改业务数据库。审计包含凭据 ID、用户 ID、方法、目标、HTTP 结果和带时区时间；不记录密码、原始令牌或请求正文。时间存储 UTC，客户端可转换北京时间。

## 配置、部署与回滚

受保护的 `service.env` 设置以下非密钥配置（地址使用已有站点，不创建公网路由）：

```ini
POLICY_ANALYSIS_AI__PUBLIC_BASE_URL=https://policy.yuan-xin.top
POLICY_ANALYSIS_AI__REQUESTS_PER_MINUTE=120
POLICY_ANALYSIS_AI__MAX_BODY_BYTES=262144
```

`public_base_url` 是 OAuth issuer/resource 的规范地址；改变地址会使旧短期令牌失效，客户端需重新换取。机器 secret 不需要配置到服务端，只在客户端注入。

1. Jenkins Verify 运行后端全量测试、静态检查及前端测试/构建。服务尚未停止。
2. `scripts/deployment.py preflight --root <部署目录>` 使用本次已验证代码读取线上配置及 `service.env`，检测 pending/running 采集/分析任务；存在任务时停止本次部署。使用 SQLite backup API 生成一致性备份，并备份配置、密码文件、环境文件到部署目录的 `src/data/deployment-backups/北京时间戳`，目录 700、文件 600。
3. systemd 优雅停服后 rsync 更新代码；整个 `src/data/`、`src/config/app.json`、`service.env`、日志和虚拟环境都保留。首次部署才初始化配置和密码文件。
4. 安装依赖，`scripts/deployment.py migrate` 使用线上数据库配置运行 Alembic `0008`。迁移只新增四张 AI 接入表，不更改业务表和历史数据。
5. `systemctl start policy-analysis`，检查 systemd 和 ready；Jenkins 成功后置步骤自动执行 `scripts/verify_ai_access.py --from-password-file`，读取线上受保护密码文件中的管理员，临时签发独立凭据并完成真实远程 MCP 验收，最后撤销凭据。验收失败会使构建失败。不要在 Jenkins 日志中展开凭据。

验收脚本从 `AI_VERIFY_BASE_URL`、`AI_VERIFY_USERNAME`、`AI_VERIFY_PASSWORD` 环境变量读取信息，运行 `python scripts/verify_ai_access.py`。会临时创建凭据、查询已有政策、创建一条无外部网络副作用的词频任务、确认完成并读取非空结果，最后撤销凭据；保留分析任务作为审计证据。测试数据为空时明确失败，不伪造通过。脚本实测 OAuth 自动发现、协议初始化、21 个工具发现、查询、写入、重试及权限拒绝。

回滚：先确认无活动任务、备份当前数据并停服，恢复上一提交代码及其依赖。新增表对旧代码无影响，通常保留数据库即可。若必须回退数据库，使用新代码环境执行 `alembic downgrade 0007`（同样注入线上数据库路径）；这会删除 AI 凭据、令牌、幂等和审计表，但不删除政策/业务任务。或在停服状态恢复备份 SQLite（同时移除对应 WAL/SHM，仅针对已确认的数据库文件）。恢复原配置及 service.env，回到旧代码时必须移除配置文件新增的 `ai` 节及 `POLICY_ANALYSIS_AI__*` 环境变量，因为旧版配置禁止未知字段；然后启服务并验证网页登录、政策检索和任务中心。整库恢复会丢弃备份后写入，优先采用保留新增表的代码回滚。

规范参考：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[MCP 授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)、[OAuth client credentials 扩展](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)。
