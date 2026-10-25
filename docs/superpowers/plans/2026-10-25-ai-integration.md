# 实施与验收计划

1. 检查 main、auth、policies、sources、tasks、analysis、迁移、配置和 Jenkins，列出复用接口与工具。
2. 先运行 `python -m pytest src/tests/backend/test_ai_access.py -q` 观察凭据入口缺失；实现四张表、迁移、凭据管理及 Bearer 权限。
3. 增加持久化幂等、限流、审计、统一错误、OpenAPI 安全声明与采集响应模型；运行 API 安全测试。
4. 使用官方 `mcp==1.30.0`，提供 Streamable HTTP、OAuth 发现/client_credentials 及结构化工具；`test_ai_mcp_http.py` 在真实 TCP 端口完成 initialize/list/query/write 和异步结果验收。
5. 补充重启、并发、过期、动态权限、迁移回滚及部署保护测试；Linux 运行完整后端测试，运行前端测试/类型检查/构建。
6. 更新接入、需求与设计文档及 Jenkins；代码审查后提交推送 main。通过 Jenkins API 构建并运行线上 AI 验收；主机不可达时明确记录阻塞，不标记已部署。
