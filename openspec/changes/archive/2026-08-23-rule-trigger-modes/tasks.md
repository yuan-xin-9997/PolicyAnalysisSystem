## 1. 后端模型与迁移

- [x] 1.1 `CollectionRule` 模型新增 `trigger_mode`/`cron_expression`/`schedule_timezone`/`schedule_enabled`/`next_run_at`/`last_run_at`，删除 `Schedule` 模型
- [x] 1.2 迁移 0006：加列、合并每规则代表计划、删除 `schedules` 表；downgrade 可逆
- [x] 1.3 模式结构与迁移往返测试更新（含 0005→0006 数据合并专测、版本号断言 0006）

## 2. 后端服务与调度

- [x] 2.1 规则 schema（Create/Update/Read）支持触发字段，manual 模式拒绝携带 Cron/启用标记，Read 暴露全部调度状态
- [x] 2.2 服务层 `_resolve_trigger_config`：Cron 5 段校验、启用定时要求规则启用、next_run 计算/清空、切回 manual 清空定时配置
- [x] 2.3 `TaskRepository` 按规则提供 `scheduled_rules`/`due_scheduled_rules`/`get_scheduled_rule`，`create_scheduled_task_once` 以规则为键去重并回写运行时间
- [x] 2.4 `TaskScheduler.sync_jobs`/`enqueue_rule`/`enqueue_due_tasks` 改为规则维度，Cron 非法跳过
- [x] 2.5 规则创建/更新路由联动 `sync_jobs`；删除 `/api/v1/schedules` 系列端点
- [x] 2.6 来源 API/服务、任务仓储/调度器测试全部更新通过，ruff 检查通过

## 3. 前端

- [x] 3.1 `CollectionRule` 类型扩展并删除 `Schedule` 类型
- [x] 3.2 规则表单支持新增/编辑、触发方式单选、Cron 校验与启用确认；manual 提交体不含定时字段
- [x] 3.3 规则列表展示触发方式/Cron/启停/下次执行，管理员行内编辑
- [x] 3.4 删除定时计划页面、组件、路由与任务中心入口；e2e mock 同步
- [x] 3.5 前端测试（rules.spec 重写覆盖定时创建/无效 Cron/编辑/只读）、type-check、lint、vitest、build、e2e 全部通过

## 4. 文档

- [x] 4.1 README 采集章节改为规则级触发方式说明与回填流程第 6 步措辞
- [x] 4.2 OpenSpec 变更归档（proposal/design/tasks/spec）
