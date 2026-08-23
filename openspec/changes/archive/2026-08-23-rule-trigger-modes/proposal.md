# 采集规则支持定时运行与手工触发两种触发方式

## Why

采集规则与定时计划此前是两个独立对象：规则只承载关键词/回填窗口等判定配置，定时运行需要在单独的「定时计划」页面为规则再创建一条 Cron 计划。管理员要理解两个概念、跨页面操作才能让一条规则定时运行，且规则与计划的启停状态彼此独立，容易出现「规则已停用但计划仍生效」之类的错配。

## What Changes

- 采集规则新增 `trigger_mode` 字段，取值 `manual`（手工触发）或 `schedule`（定时运行），把触发方式收敛为规则自身属性。
- 定时运行所需的 `cron_expression`（北京时间 5 段）、`schedule_timezone`、`schedule_enabled`、`next_run_at`、`last_run_at` 全部并入 `collection_rules` 表；迁移 0006 将存量 `schedules` 数据（每规则取一条代表计划，优先启用的）合并进规则后删除该表。
- 规则创建/更新 API 支持触发方式配置：schedule 模式必须提供合法 Cron，manual 模式不得携带 Cron 或启用标记；切换回 manual 自动清空定时配置；启用定时的规则必须处于启用状态。
- 调度器改为按规则注册 APScheduler 任务（job id `rule:{id}`），任务创建与去重以 `(trigger_type='schedule', rule_id, scheduled_for)` 为键，触发后回写规则的 `last_run_at` 与 `next_run_at`。
- 手工触发能力保持不变：任何模式（含定时）的规则都可通过 `POST /api/v1/tasks` 立即执行一次。
- 前端「采集规则」表单支持新增与编辑，内嵌触发方式单选、Cron 输入与「启用定时运行」确认流程；规则列表展示触发方式、Cron、启停与下次执行时间；删除独立的「定时计划」页面、路由与 `/api/v1/schedules` 接口。

## Capabilities

### New Capabilities

- `collection-rules`: 定义采集规则的两种触发方式、定时配置约束、调度与手工触发行为及管理界面要求。

### Modified Capabilities

无。

## Impact

- 后端：`policy_analysis.sources`（模型、schema、服务、仓储、路由）与 `policy_analysis.tasks`（仓储、调度器）；新增 Alembic 迁移 0006。
- 数据：`collection_rules` 增加 6 列；`schedules` 表数据合并后删除；`crawl_tasks.trigger_type` 语义不变。
- 前端：规则表单与列表、任务中心入口链接、路由与 API 类型；删除定时计划相关组件与测试。
- 测试与文档：更新来源 API/服务、调度器、模式结构、迁移往返与前端规则测试；同步 README 采集章节。
