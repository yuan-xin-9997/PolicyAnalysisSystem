# AI 接入设计说明

保留 FastAPI/Vue/SQLite、现有 `/api/v1` 和用户页面权限；增加独立 Bearer 凭据及官方 MCP SDK 的 Streamable HTTP 传输。AI API 不建立旁路业务服务。

## 需求与范围

开放政策检索/详情、来源与类别、规则查询/创建/修改、异步采集、异步分词/政策比对、任务进度/日志/结果。默认不开放删除政策、发布、批量修改、权限管理或配置写入。兼容现有网页 Cookie + CSRF。机器授权为 scope 与所属用户实时权限交集，管理员业务仍要求管理员身份。

## 方案选择

独立 MCP 进程会增加配置、运维和业务鉴权重复；另建 AI CRUD API 会增加权限差异。采用同进程 SDK 传输，通过 ASGI 内部调用现有受认证 API；只有凭据、OAuth、幂等和审计元数据新增持久化代码。

## 组件与数据流

`ai/models.py`：四张新增表。`service.py`：凭据摘要、授权身份与 scope。`middleware.py`：机器边界、限流、写入幂等和审计。`routes.py`：Cookie 保护的凭据管理、OAuth 发现与 client_credentials 令牌。`mcp.py`：21 个具明确输入模型与属性的工具，调用相同 API。`openapi.py`：统一安全方案、错误与示例。`tasks/routes.py`：补齐原有采集响应模型。

凭据随机生成、明文仅返回一次；OAuth 令牌独立随机生成并绑定规范 MCP resource，最长 15 分钟。每次验证父凭据撤销/过期及用户状态。client_credentials 是明确支持的机器扩展，不宣称交互 OAuth 客户端兼容。远程使用现有 HTTPS 站点，Host 绑定配置地址且拒绝 Origin。

幂等账本在业务执行前唯一占位，完成后持久保存响应；并发冲突、不同参数或崩溃未完成记录一律 409，不自动再次执行。跨业务提交/响应记录的崩溃窗口采用人工核查策略。限流为单进程固定分钟窗口，容量有界。审计只含身份、操作、目标、状态、时间。

## 验证与运维

测试凭据成功/错误/撤销/过期/动态越权、确认参数、幂等冲突与重启、限流、OpenAPI。真实 TCP + 官方 OAuth MCP 客户端执行发现、initialize、tools/list、查询、写入、重试与异步终态结果。迁移验证升级/回退/再升级。Jenkins 增加活动任务检查及一致性备份，保留整个 data 和线上配置。

详细范围、工具清单、示例、迁移/回滚与限制见 `docs/ai-integration.md`；实际验证、提交和部署状态见 `docs/ai-delivery-status.md`。
