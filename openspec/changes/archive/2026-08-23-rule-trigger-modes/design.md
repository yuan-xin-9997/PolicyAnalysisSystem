# 采集规则触发方式设计

## 目标与非目标

**目标**：把定时运行与手工触发两种触发方式统一为采集规则的自身属性，消除独立的定时计划对象；保持任务执行语义（快照、去重、取消、恢复）不变。

**非目标**：不改变 `crawl_tasks` 的结构或 `trigger_type` 语义；不引入多计划（一条规则多个 Cron）能力；不改变手工触发的权限与 CSRF 要求。

## 数据模型

`collection_rules` 新增列（迁移 0006）：

| 列 | 类型 | 约束 |
| --- | --- | --- |
| `trigger_mode` | TEXT(16) NOT NULL | 默认 `'manual'`；取值由服务层校验 |
| `cron_expression` | TEXT(128) NULL | schedule 模式必填（服务层校验） |
| `schedule_timezone` | TEXT(64) NOT NULL | 固定 `'Asia/Shanghai'` |
| `schedule_enabled` | BOOL NOT NULL | 默认 0 |
| `next_run_at` / `last_run_at` | TEXT(40) NULL | UTC ISO 文本，与既有 UTCDateTime 一致 |

新列不加表级 CHECK：SQLite 无法通过 ALTER TABLE 附加表级约束，重建表会牵连三张外键引用表；取值合法性全部在服务边界（Pydantic Literal + `_resolve_trigger_config`）保证。`test_alembic_collection_schema_matches_orm_and_round_trips` 继续逐表比对 ORM 与迁移的签名一致性。

存量合并：每个规则取一条代表计划（`ORDER BY is_active DESC, id LIMIT 1`），写入规则的定时字段并把 `trigger_mode` 置为 `schedule`；没有计划的规则保持 manual。合并后 `DROP TABLE schedules`。downgrade 重建空 `schedules` 表并删除新列。

## 服务层校验（`_resolve_trigger_config`）

- manual 模式：`cron_expression` 与 `schedule_enabled` 必须为空/假，否则 `RULE_TRIGGER_INVALID`；
- schedule 模式：必须提供 5 段合法 Cron（`INVALID_CRON`）；`schedule_enabled=true` 要求规则 `is_active=true`；
- 启用定时或修改 Cron 时基于服务时钟重算 `next_run_at`，停用清空；
- 更新切换到 manual 时清空 Cron、`schedule_enabled` 与 `next_run_at`，保留 `last_run_at` 作为历史。

规则创建/更新成功后由路由层触发 `TaskScheduler.sync_jobs()`，与原计划接口的联动方式一致。

## 调度

- `TaskRepository.scheduled_rules()` / `due_scheduled_rules()` / `get_scheduled_rule()` 以 `trigger_mode='schedule' AND schedule_enabled AND is_active` 过滤；
- `sync_jobs()` 为每条启用的定时规则注册 APScheduler Cron 任务（job id `rule:{id}`，时区取 `schedule_timezone`）；Cron 非法时跳过该规则而不是让调度器崩溃；
- `create_scheduled_task_once(rule_id, scheduled_for, now, next_run_at=...)` 在锁内校验规则仍启用、按 `(trigger_type, rule_id, scheduled_for)` 去重创建任务，并在成功后回写 `last_run_at` 与 `next_run_at`（下次触发时间由调度器用 CronTrigger 计算），使列表展示的「下次执行」在每次触发后保持新鲜。

## 前端

- `RuleFormDialog` 同时承担新增与编辑（`rule` prop 预填，`key` 强制切换时重建）；触发方式为单选组，schedule 模式展开 Cron 输入与「启用定时运行」勾选；从停用切换为启用时弹出确认（与原计划页一致的文案）；manual 模式提交体不携带 `cron_expression`/`schedule_enabled`。
- `RuleListView` 增加「触发方式」「定时配置」列与行内「编辑」按钮（仅管理员）；空行 colspan 随管理员列动态调整。
- 删除 `ScheduleListView`/`ScheduleFormDialog`、`/tasks/schedules` 路由、任务中心入口链接与 `Schedule` 类型。

## 风险与兼容

- 迁移为全量回写单表 + DROP，SQLite 下均为 O(1) DDL/UPDATE，风险低；有专门的 0005→0006 数据合并测试。
- 旧客户端调用 `/api/v1/schedules` 将得到 404：系统无外部 API 消费方，前端同步替换。
- `enqueue_due_tasks` 轮询路径（原基于 `next_run_at` 兜底）保留并改为规则维度，行为与原一致。
