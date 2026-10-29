# 采集规则触发方式规格

## Purpose
定义采集规则的两种触发方式（手工触发与定时运行）、定时配置约束、调度执行行为与管理界面要求，确保任何一条规则的运行方式都是其自身明确且可审计的属性。
## Requirements

### Requirement: 自定义采集类别
系统 SHALL 允许管理员在新增或编辑规则时选择已有类别，或直接输入新类别名称；SHALL 以同一事务保存类别和规则，同名类别复用；SHALL 保持文章归档、类别筛选与既有规则兼容。类别用于归档，采集范围仍由包含词、排除词及入口配置决定。

#### Scenario: 新建中央委员会全会规则
- **WHEN** 管理员选择“新建类别…”并输入“中央委员会全会”，提交有效规则
- **THEN** 系统创建类别并绑定规则，将新类别加入可复用的下拉选项

#### Scenario: 类别校验与原子保存
- **WHEN** 请求同时提供类别编码和新名称，或未选择类别，或名称为空白或超过 256 字符
- **THEN** 系统拒绝请求；任何规则校验失败均不遗留新类别

#### Scenario: 同名类别复用与权限
- **WHEN** 管理员输入一个已有类别的名称
- **THEN** 系统复用该类别并继续校验启用状态；普通用户不能创建类别或保存规则

### Requirement: 采集规则的两种触发方式
采集规则 SHALL 具有唯一且必填的触发方式属性，取值 SHALL 限定为 `manual`（手工触发）或 `schedule`（定时运行）；`manual` 规则 SHALL 只能通过任务中心手工创建采集任务，`schedule` 规则 SHALL 由调度器按 Cron 自动创建采集任务且同时保留手工触发能力。

#### Scenario: 默认手工触发
- **WHEN** 管理员创建规则且未指定触发方式
- **THEN** 规则以 `manual` 保存，系统不会为其自动创建任何采集任务

#### Scenario: 定时规则也可手工触发
- **WHEN** 管理员对处于定时运行模式的规则调用手工触发
- **THEN** 系统立即创建一个 `trigger_type=manual` 的采集任务，不影响其定时计划

### Requirement: 触发方式配置约束
系统 SHALL 在规则创建与更新时校验触发配置：`schedule` 模式 SHALL 要求合法的北京时间 5 段 Cron 表达式；`manual` 模式 SHALL 拒绝携带 Cron 表达式或启用定时的请求；启用定时的规则 SHALL 处于启用状态。

#### Scenario: 定时模式缺少或非法 Cron
- **WHEN** 管理员提交 `trigger_mode=schedule` 而未提供 Cron，或 Cron 不是 5 段/字段非法
- **THEN** 系统拒绝请求并返回稳定的 `INVALID_CRON` 或 `RULE_TRIGGER_INVALID` 错误

#### Scenario: 手工模式携带定时字段
- **WHEN** 管理员对 manual 规则提交 `cron_expression` 或 `schedule_enabled=true`
- **THEN** 系统拒绝请求并返回 `RULE_TRIGGER_INVALID`

#### Scenario: 启用定时的规则必须启用
- **WHEN** 管理员尝试启用一条已停用规则的定时运行，或停用一条定时已启用的规则
- **THEN** 系统拒绝请求并提示先调整启用状态

#### Scenario: 切换回手工触发清空定时配置
- **WHEN** 管理员把 `schedule` 规则切换为 `manual` 且请求仅携带 `trigger_mode`
- **THEN** 系统清空该规则的 Cron、定时启用标记与下次执行时间，保留上次执行时间作为历史

### Requirement: 规则级调度执行
调度器 SHALL 仅为 `trigger_mode=schedule`、`schedule_enabled` 与 `is_active` 均为真的规则注册定时任务；同一规则同一计划触发时刻 SHALL 只创建一个 `trigger_type=schedule` 的采集任务；每次触发后系统 SHALL 回写规则的上次执行时间并按 Cron 重算下次执行时间。

#### Scenario: 到点触发一次
- **WHEN** 定时规则到达 Cron 触发时刻且当刻尚无对应任务
- **THEN** 系统创建一个 `trigger_type=schedule` 的采集任务并唤醒工作线程

#### Scenario: 重复到点不重复建任务
- **WHEN** 同一触发时刻被重复判定为到期
- **THEN** 系统不重复创建任务，规则的上次/下次执行时间保持一致

#### Scenario: 规则停用后不再定时执行
- **WHEN** 规则被停用或定时被停用
- **THEN** 调度器不再为其创建定时任务，已有任务不受影响

### Requirement: 触发方式管理界面
采集规则页面 SHALL 为管理员提供触发方式配置（单选手工触发/定时运行、定时 Cron 输入、启用定时勾选），SHALL 支持编辑既有规则，SHALL 在首次启用定时时要求确认；规则列表 SHALL 展示触发方式、Cron、定时启停与下次执行时间（北京时间）；系统 SHALL 不再提供独立的定时计划页面或接口。

#### Scenario: 配置定时规则
- **WHEN** 管理员在规则表单选择定时运行、填写合法 Cron 并勾选启用定时后保存
- **THEN** 系统弹出启用确认，确认后保存规则并展示 Cron、已启用与下次执行时间

#### Scenario: 客户端 Cron 预校验
- **WHEN** 管理员在定时模式输入非 5 段 Cron 并提交
- **THEN** 页面在客户端提示错误且不发起保存请求

#### Scenario: 编辑既有规则
- **WHEN** 管理员在规则列表点击编辑并修改后保存
- **THEN** 系统通过规则更新接口保存变更并刷新列表行

#### Scenario: 旧定时计划入口不可用
- **WHEN** 用户访问原定时计划路由或客户端调用 `/api/v1/schedules`
- **THEN** 前端不再提供入口，接口返回 404
