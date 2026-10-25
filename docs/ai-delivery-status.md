# AI 接入交付状态（2026-10-25，北京时间）

## 已实现

- 独立、带 scope/过期时间、可撤销的凭据；明文仅创建时返回，数据库仅存摘要。
- 复用 `/api/v1` 核心业务、原用户/页面权限及异步任务执行器；不允许机器凭据管理用户、配置或凭据。
- 官方 `mcp==1.30.0`、Streamable HTTP、21 个工具；OAuth 元数据发现、client_credentials 扩展、短期令牌及 resource 绑定。
- 持久化幂等、参数和确认校验、限流、统一错误、API 与 MCP 写入审计。
- 新增 0008 迁移、回退方案、部署前任务检查与 SQLite 一致性备份；Jenkins 保留整个 data 和线上配置，部署成功后执行真实 OAuth/MCP 语义验收。
- [接入文档](ai-integration.md)、[需求规格](../openspec/specs/ai-integration/spec.md)、[设计说明](superpowers/specs/2026-10-25-ai-integration-design.md)。

## 已验证

- Windows 新增接入/迁移/备份/真实 MCP 集成测试：13 项通过。
- Linux（WSL Ubuntu，Python 3.12.3）：最终完整回归 **753 项全部通过，0 失败**，耗时 256.43 秒。覆盖率实测 94.66%，达到 80% 门槛。原测试中的最新迁移版本断言已同步更新到 0008。
- 官方 Python 客户端通过真实 TCP 完成 OAuth 发现/换令牌、initialize、tools/list、含时间范围的政策查询、创建分析任务、重试返回相同 ID、异步成功并读取非空词频结果。
- 匿名、空/错误/过期/撤销凭据、越权、MCP 子路径匿名发现、超大分块 OAuth 正文、非法工具名称、幂等冲突/并发被正确拒绝。
- 新应用实例保留完成结果，恢复排队任务，并为异常中断任务标记失败原因；幂等响应在重启后重放。
- Alembic 0007→0008→0007→0008 保留原业务数据；活动任务阻止部署，一致性备份可读取。
- 前端 73 项单元测试、类型检查、ESLint、生产构建通过。Windows 的原 `npm run lint` shell 路径写法不可用，直接调用相同 ESLint 程序与相同配置/范围通过。
- Ruff 检查、格式检查、git diff 空白检查通过；独立审查发现项已修复并回归。

## 已部署并完成线上验收

主机恢复连通后，已通过 Jenkins API 触发构建。首次构建 39 在停服前发现 console `pytest` 的脚本模块路径问题；补齐 pytest 根路径及 scripts 包后，构建 **40 SUCCESS**，实现部署提交 **f780237**。线上 Jenkins 再次运行后端 753 项、前端 73 项测试及静态检查/构建，全部通过。

生产服务使用现有 systemd、端口和 HTTPS 站点，没有新增公网映射。在受保护的 `service.env` 设置规范地址，保留线上 `app.json`。部署前无 pending/running 任务；备份包括：

- 配置变更前：`/opt/PolicyAnalysisSystem/src/data/deployment-backups/pre-ai-20261025-105636`
- 流水线升级前：`/opt/PolicyAnalysisSystem/src/data/deployment-backups/20261025-121231-090651`

线上 SQLite `integrity_check=ok`，版本为 0008；升级前后均为 **70 条政策**，两份备份的 `app.json` 与线上文件摘要一致。验收临时凭据已撤销，审计可查到 MCP 写调用。

构建 40 的真实 HTTPS 验收结果：协议 `2025-11-25`，发现 **21 个工具**，查询成功，创建分析任务 **6**，状态 **succeeded**，返回 **20 条词频结果**；重试返回相同任务 ID。匿名、错误凭据、scope 越权与撤销凭据被拒绝。独立外部复核确认匿名 API、规范 MCP 路径及 MCP 子路径均返回 401。

已上线地址：

- API：`https://policy.yuan-xin.top/api/v1`
- OpenAPI：`https://policy.yuan-xin.top/openapi.json`
- 交互文档：`https://policy.yuan-xin.top/docs`
- MCP：`https://policy.yuan-xin.top/mcp/`

文档同步提交后的构建仍会重复执行安全检查、备份、升级及语义验收。具体最新构建号可见 Jenkins；本记录中的构建 40 是首次成功部署与完整线上验收的证据。

## 明确限制

未实现交互式 authorization_code/PKCE、动态客户端注册和刷新令牌，不声明直接兼容要求该流程的 ChatGPT/Claude/Cursor 客户端。已实测的是官方 Python client_credentials 扩展客户端。幂等崩溃窗口采用保守阻止重复执行策略，需人工核查；限流为当前单进程部署设计。部署与回滚步骤详见接入文档。
