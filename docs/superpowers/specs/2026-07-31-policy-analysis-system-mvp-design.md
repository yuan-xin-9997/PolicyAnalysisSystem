# 政策分析系统首期 MVP 设计说明

- 系统中文名：政策分析系统
- 系统英文名：PolicyAnalysisSystem
- 文档日期：2026-07-31
- 文档状态：已完成用户设计确认
- 目标版本：首期 MVP

## 1. 背景

本系统用于抓取、存储和检索中国政府机构发布的政策信息，并为后续词频对比、政策表述变化分析、邮件推送和大模型预测提供数据基础。

首期选择新华网发布的「中共中央政治局召开会议」官方新闻通报稿作为纵向验证场景。系统需要回填执行任务当日向前滚动 5 年内的通报，并持续发现后续新增通报。

## 2. 首期目标与边界

### 2.1 目标

首期交付一个可部署、可登录、可配置、可执行和可检索的完整 MVP，包含以下能力：

1. 登录、退出和会话管理。
2. 用户角色与页面权限管理。
3. 系统配置查看与敏感信息脱敏。
4. 新华网采集规则、历史回填、定时增量采集和手工触发。
5. 任务状态、进度、统计、明细与日志查看。
6. 政策去重入库、组合检索和正文详情查看。
7. 推送管理与政策分析的占位页面。
8. Windows、Linux 启停脚本，Linux systemd 服务和 Jenkins 部署流水线。

### 2.2 非目标

首期不实现以下能力：

- 实际邮件推送；
- 词频对比、表述差异和可视化分析；
- 大模型调用及政策预测；
- 新华网之外的政府网站采集适配器；
- 分布式任务队列、多节点部署和 PostgreSQL；
- 依赖外部搜索引擎的生产期文章发现。

占位页面不得提供无法执行的按钮，只展示规划范围和「功能规划中」状态。

### 2.3 验收结果

首期验收以以下结果为准：

- 默认管理员能够登录并访问全部页面；
- 普通用户只能访问管理员授权的页面，前后端权限结果一致；
- 手工任务能够处理滚动近 5 年的目标通报；
- 历史回填时，处于窗口内的已核验种子数必须等于写入、更新和重复项总数；任何已核验种子被过滤或抓取失败都视为验收失败；
- 相同任务重复执行不会产生重复政策；
- 政策可以按关键词、发布时间、抓取时间、发布部门、类别和来源检索；
- 任务中心能够展示进度、统计、日志和候选文章处理结果；
- 敏感配置始终脱敏，服务重启后配置、数据和计划保持不变；
- Jenkins 部署成功后，可通过配置的主机和端口访问系统。

## 3. 方案选择

### 3.1 已选方案

采用「官方入口增量采集 + 受控历史回填」：

- 首次回填使用随代码发布、经过核验的历史种子 URL 清单；
- 后续增量采集读取新华网官方时政 RSS 和配置的栏目入口；
- 所有正文获取统一调用 WebFetch，不在业务系统中重复实现 HTTP、浏览器、重试、限速和通用正文提取；
- 生产运行时不依赖搜索引擎；搜索引擎只可在维护种子清单时作为人工发现辅助，最终 URL 必须由维护者核验为新华网官方文章。

### 3.2 未选方案

| 方案 | 优点 | 未采用原因 |
| --- | --- | --- |
| 全站栏目递归抓取 | 自动发现历史文章 | 新旧页面和 URL 结构不同，容易漏抓并产生大量无关请求 |
| 外部搜索引擎辅助发现 | 历史覆盖可能更高 | 增加第三方依赖、配额和结果不稳定性 |

新华网旧版文章与当前文章使用不同页面结构，可参考 [2021 年页面](https://www.news.cn/2021-10/18/c_1127969449.htm) 和 [当前页面](https://www.news.cn/politics/leaders/20260227/a8b27b1b8c7442be9678ff6e530cdd18/c.html)。新华网公开的时政 RSS 入口可参考 [新华网链接说明](https://www.news.cn/linktous.htm)。

## 4. 总体架构

系统采用 FastAPI + Vue 3 + SQLite 的模块化单体架构。生产环境由 FastAPI 同时提供 `/api/v1` REST API 和 Vue 构建后的静态资源，统一使用配置中的端口，首期端口为 `30080`。

```text
浏览器
  └─ Vue 3 SPA
      └─ FastAPI /api/v1
          ├─ 认证与权限
          ├─ 政策目录
          ├─ 来源与采集规则
          ├─ 调度器与任务执行器
          ├─ 系统配置
          └─ SQLite（WAL）
                 │
                 └─ WebFetch REST API
                        └─ 新华网官方页面
```

### 4.1 架构约束

- 使用 Python 3.12 运行后端，使用 Vue 3 + TypeScript 构建前端。
- 首期使用 SQLite，并开启 WAL、外键和合理的 busy timeout。
- 生产环境只启动 1 个 FastAPI 进程，避免内嵌调度器重复触发。
- 后台任务使用受控线程池，不引入 Celery、Redis 或独立消息队列。
- WebFetch 是唯一的网页正文抓取入口。接口说明以 [WebFetch README](https://github.com/yuan-xin-9997/web_fetch/blob/main/README.md) 和 OpenAPI 契约为准。
- 环境相关值全部来自配置文件或环境变量，业务代码中不得硬编码主机、端口、账号、密码或绝对路径。

### 4.2 模块边界

后端模块按职责拆分：

| 模块 | 职责 | 主要依赖 |
| --- | --- | --- |
| `auth` | 密码文件同步、登录、退出、会话 | 用户仓储、密码文件 |
| `rbac` | 角色、页面权限、API 权限 | 用户仓储 |
| `policies` | 政策写入、去重、检索、详情 | SQLite |
| `sources` | 来源、类别、规则、种子 URL | SQLite |
| `collectors` | 来源适配器和新华网判定规则 | WebFetch 客户端 |
| `tasks` | 任务、明细、日志、取消、重跑 | SQLite、采集器 |
| `scheduler` | Cron 计划加载、触发和互斥 | 任务服务 |
| `settings` | 配置加载、校验、脱敏展示 | `app.json`、环境变量 |
| `system` | 健康检查、版本信息 | 数据库、构建信息 |

模块之间通过服务接口通信。采集器不得直接写任务表或用户表；任务服务负责状态流转，政策服务负责事务性入库。

## 5. 页面与交互

### 5.1 登录页

- 输入用户名和密码；
- 登录失败使用统一提示，不区分用户不存在或密码错误；
- 登录成功后进入第一个有权访问的业务页面；
- 已登录用户访问登录页时跳转到业务首页。

### 5.2 政策数据库

列表筛选项：

- 关键词，匹配标题和正文；
- 发布时间起止；
- 抓取时间起止；
- 发布部门；
- 政策类别；
- 来源。

列表支持分页、发布时间排序和抓取时间排序。详情页展示标题、正文、来源链接、发布部门、类别、发布时间、首次抓取时间、最近抓取时间、内容指纹和最近关联任务。

正文以纯文本安全渲染，不执行来源 HTML、JavaScript 或内嵌资源。

### 5.3 任务中心

任务中心包含 4 个子页面：

1. 采集规则：维护来源、类别、关键词、排除词、历史窗口和启用状态。
2. 定时计划：维护 5 段 Cron 表达式、启用状态和下次执行时间。
3. 任务列表：按状态、规则、触发方式和时间筛选。
4. 任务详情：展示进度、汇总统计、任务日志和候选文章处理明细。

管理员可以手工触发、取消等待中的任务，以及请求运行中任务在当前候选项结束后取消。任务详情通过短轮询刷新，首期不引入 WebSocket。

### 5.4 权限管理

系统支持 `admin` 和 `user` 两种角色：

- `admin` 始终拥有所有页面和 API 权限；
- `user` 的页面权限由管理员逐项配置；
- 页面菜单隐藏与后端 API 鉴权使用同一组权限代码。

权限页支持新增用户、重置密码、修改角色、启用或停用用户、配置页面权限。用户和密码变更必须通过原子写方式同步到 `password.txt`，不得在界面返回现有明文密码。

### 5.5 系统配置

配置页只读展示 `app.json` 与环境变量覆盖后的生效值。字段标明来源是「配置文件」「环境变量」或「默认值」。字段名命中 `password`、`secret`、`token`、`api_key` 等敏感模式时，只返回掩码。

### 5.6 导航与版本

左侧栏底部固定显示：

- 当前登录用户名；
- 退出按钮；
- `v0.<Git 提交总数>` 版本号。

系统信息接口同时返回短提交 SHA。Jenkins 注入版本环境变量；本地开发时可从 Git 只读获取，无法获取时使用明确的开发版本标识。

## 6. 数据设计

所有时间字段保存带时区的 ISO 8601 值，业务展示统一转换为 `Asia/Shanghai`。SQLite 连接启用外键。

### 6.1 用户与会话

#### `users`

- `id`
- `username`，唯一
- `password_hash`
- `role`，取值 `admin` 或 `user`
- `is_active`
- `created_at`
- `updated_at`
- `password_synced_at`

#### `page_permissions`

- `user_id`
- `page_code`
- 联合唯一键：`user_id + page_code`

#### `sessions`

- `id`
- `user_id`
- `token_hash`
- `csrf_token_hash`
- `expires_at`
- `created_at`
- `last_seen_at`

Cookie 只保存不可预测的会话令牌，数据库只保存令牌哈希。

### 6.2 来源与规则

#### `policy_categories`

- `id`
- `code`，唯一
- `name`
- `description`
- `is_active`

#### `sources`

- `id`
- `code`，唯一
- `name`
- `organization`
- `base_url`
- `adapter_type`
- `allowed_domains_json`
- `is_active`

#### `collection_rules`

- `id`
- `source_id`
- `category_id`
- `name`
- `include_keywords_json`
- `exclude_keywords_json`
- `history_years`，首期默认值为 `5`
- `discovery_config_json`
- `is_active`
- `created_at`
- `updated_at`

#### `seed_urls`

- `id`
- `rule_id`
- `url`
- `expected_title`
- `expected_published_date`
- `is_verified`
- `created_at`
- 唯一键：`rule_id + url`

#### `schedules`

- `id`
- `rule_id`
- `cron_expression`
- `timezone`，首期固定为 `Asia/Shanghai`
- `is_active`
- `next_run_at`
- `last_run_at`

### 6.3 政策

#### `policies`

- `id`
- `source_id`
- `category_id`
- `title`
- `canonical_url`
- `publisher`
- `published_at`
- `content_text`
- `content_hash`
- `webfetch_artifact_id`
- `first_crawled_at`
- `last_crawled_at`
- `created_at`
- `updated_at`

约束与索引：

- `source_id + canonical_url` 唯一；
- `source_id + content_hash` 建立索引，用于识别不同 URL 的同内容稿件；
- `published_at`、`last_crawled_at`、`publisher` 和 `category_id` 建立检索索引；
- 标题和正文首期使用 SQLite FTS5 支持关键词检索。

#### `policy_revisions`

正文发生变化时，在覆盖当前正文前保存旧版本：

- `id`
- `policy_id`
- `content_text`
- `content_hash`
- `webfetch_artifact_id`
- `replaced_at`
- `task_item_id`

### 6.4 任务与日志

#### `crawl_tasks`

- `id`
- `rule_id`
- `trigger_type`，取值 `manual` 或 `schedule`
- `status`
- `requested_by`
- `scheduled_for`
- `started_at`
- `finished_at`
- `cancel_requested_at`
- `request_snapshot_json`
- `discovered_count`
- `success_count`
- `duplicate_count`
- `filtered_count`
- `failed_count`
- `error_summary`

任务状态取值：

```text
pending → running → succeeded
                  → partially_succeeded
                  → failed
                  → cancelled
pending           → cancelled
```

#### `crawl_task_items`

- `id`
- `task_id`
- `candidate_url`
- `normalized_url`
- `status`，取值 `stored`、`updated`、`duplicate`、`filtered` 或 `failed`
- `policy_id`
- `attempt_count`
- `reason_code`
- `reason_message`
- `started_at`
- `finished_at`

#### `crawl_task_logs`

- `id`
- `task_id`
- `level`
- `message`
- `context_json`，写入前脱敏
- `created_at`

## 7. 采集设计

### 7.1 历史种子清单

代码资源目录包含一份版本化的新华网中央政治局会议种子清单。每条记录至少包含 URL、预期标题和预期发布日期。首次部署或初始化时幂等导入数据库；后续版本可以增加清单条目，但不得删除现场自行增加的种子。

清单维护流程：

1. 通过人工检索发现候选文章；
2. 确认域名属于允许列表；
3. 确认正文是新华社官方通报，而不是视频、评论、摘要或转载；
4. 记录规范化 URL、标题和发布日期；
5. 使用测试验证清单无重复且处于滚动 5 年范围内。

运行任务时仍会按当日滚动窗口过滤。种子记录超过窗口后保留在数据库中，但不再抓取。

### 7.2 增量发现

增量发现读取规则中配置的 RSS 和栏目入口，通过 WebFetch 获取链接列表。候选 URL 必须满足：

- 主机名位于来源允许域名列表；
- URL 使用 `http` 或 `https`；
- 标题包含配置的包含关键词，且不包含排除词；
- 规范化后尚未在本任务中处理。

系统不从文章正文继续递归发现链接，避免形成无边界爬取。

### 7.3 正文抓取与判定

候选文章调用 WebFetch 文章提取接口，并要求保存 artifact。请求携带的 API Key 只从运行配置读取，不进入任务日志。

PolicyAnalysisSystem 保存标准化正文、内容哈希和 WebFetch artifact ID，不重复下载 WebFetch 管理的原始响应文件。如果后续功能确需下载文件，必须按 `src/data/YYYY/MM/DD/` 保存。

中央政治局会议通报的首期判定条件：

1. 来源域名属于新华网允许列表；
2. 标题包含「中共中央政治局召开会议」；
3. 正文导语包含「中共中央政治局」和「召开会议」；
4. 正文存在「新华社北京」或页面来源字段为「新华网」「新华社」；
5. 发布时间位于任务执行时向前滚动 5 年的闭区间内；
6. 正文长度达到配置的最小阈值；
7. 页面不是仅含编导信息的音视频稿。

判定规则由新华网适配器实现，关键词和长度阈值可配置。每个失败条件对应稳定的 `reason_code`，便于测试和任务中心解释。

### 7.4 URL 规范化与去重

规范化规则：

- 主机名转为小写；
- 移除 URL fragment；
- 移除已配置的跟踪参数；
- 保留影响正文定位的查询参数；
- 统一默认端口；
- 不擅自将旧版 `xinhuanet.com` URL 改写成新版 `news.cn` URL。

去重顺序：

1. 任务内规范化 URL 去重；
2. 数据库按 `source_id + canonical_url` 去重；
3. 按内容哈希识别不同 URL 的同内容稿件；
4. 同 URL、同哈希只更新 `last_crawled_at`；
5. 同 URL、不同哈希先写入 `policy_revisions`，再更新当前正文。

### 7.5 任务执行

1. 创建 `pending` 任务并持久化请求参数快照。
2. 工作线程通过原子状态更新领取任务。
3. 计算北京时间下的滚动 5 年边界。
4. 合并历史种子和增量入口发现的候选 URL。
5. 逐项抓取、校验、去重并使用短事务入库。
6. 每个候选项结束后检查取消标记。
7. 汇总统计并设置终态。

同一规则最多存在 1 个 `running` 任务。手工触发与定时触发冲突时，新任务进入 `pending`；相同规则、相同计划时间的重复调度请求直接返回已有任务。

## 8. 调度与恢复

调度器使用数据库中的计划作为真实来源，服务启动时重新加载。Cron 使用标准 5 段表达式，并按 `Asia/Shanghai` 计算下次运行时间。

首次初始化的定时计划默认停用。管理员完成 WebFetch 连通性检查和一次手工回填后，再在任务中心启用计划，避免首次部署立即产生未确认的外部请求。

生产环境只允许 1 个应用进程。任务线程池大小由配置控制，首期默认值不超过 `2`。SQLite 写入保持短事务，避免抓取网络请求期间占用数据库事务。

服务启动时执行恢复检查：

- 遗留的 `running` 任务转为 `failed`；
- `error_summary` 记录「服务异常中断」；
- 未领取的 `pending` 任务继续等待；
- 管理员可以从失败任务创建一次新任务，原任务记录不修改。

## 9. 错误处理

### 9.1 重试策略

以下错误最多重试 3 次，并使用带抖动的指数退避：

- 连接超时和读取超时；
- HTTP 429；
- WebFetch 5xx；
- 明确标记为可重试的临时错误。

以下情况不重试：

- 域名不在允许列表；
- 参数或配置无效；
- 页面内容不符合通报判定规则；
- WebFetch 返回不可重试的 4xx；
- 日期超出滚动窗口。

候选项失败不终止整批任务。只要至少有 1 项成功或可解释地被过滤，同时存在失败项，任务终态为 `partially_succeeded`。来源入口完全不可用、配置无效或数据库关键写入失败时，任务为 `failed`。

### 9.2 API 错误格式

API 使用统一错误结构：

```json
{
  "error": {
    "code": "TASK_ALREADY_RUNNING",
    "message": "该采集规则已有运行中的任务。",
    "request_id": "01J...",
    "details": {}
  }
}
```

响应不得包含堆栈、SQL、密钥或内部绝对路径。

## 10. 配置与安全

### 10.1 配置来源

配置优先级从高到低：

1. `POLICY_ANALYSIS_` 前缀的环境变量；
2. `src/config/app.json`；
3. 代码中的非环境默认值。

`app.json` 保存服务器、数据库相对路径、日志、WebFetch 地址引用、任务并发、重试和会话有效期等非敏感配置。WebFetch API Key 和会话密钥从 systemd 环境文件注入。仓库只提供无密钥示例，不提交生产值。

部署脚本首次部署时创建 `app.json`；增量部署不得覆盖已有文件。

### 10.2 密码文件

`src/data/password.txt` 是可登录用户凭据的持久来源，首次部署按项目要求创建默认管理员。文件权限在 Linux 上设置为 `0600`。

启动时同步密码文件；每次登录先检查文件修改时间，只有文件发生变化时才重新同步：

- 新用户写入 Argon2 密码哈希和文件中的初始角色；
- 密码变化时更新哈希；
- 角色变化时更新数据库角色；切换为管理员后自动拥有全部页面权限；
- 文件中不存在的用户不能登录，但数据库记录保留用于审计；
- 权限页创建用户、重置密码或修改角色时，先加文件锁，再写临时文件并保留原文件备份；
- 更新流程使用「数据库事务 + 文件原子替换 + 失败补偿」：数据库提交失败时恢复原文件，进程在极端中断后由下次启动同步消除差异；
- 操作全部完成后删除临时文件和备份，失败时返回统一错误并记录脱敏审计日志。

### 10.3 会话和请求安全

- 密码使用 Argon2id 哈希；
- 会话 Cookie 使用 `HttpOnly`、`SameSite=Lax`，`Secure` 可由部署配置强制启用；
- 状态变更请求校验 CSRF Token；
- 登录按账号和客户端地址限速；
- 所有管理 API 在后端校验角色或页面权限；
- 前端菜单隐藏只用于体验，不构成安全边界；
- 正文按纯文本渲染；
- WebFetch 目标 URL 再经过业务允许域名校验；
- 日志写入前按字段名和已加载密钥值脱敏。

## 11. API 边界

首期 API 使用 `/api/v1` 前缀，主要资源如下：

| 方法与路径 | 用途 | 权限 |
| --- | --- | --- |
| `POST /auth/login` | 登录 | 匿名 |
| `POST /auth/logout` | 退出 | 已登录 |
| `GET /auth/me` | 当前用户与页面权限 | 已登录 |
| `GET /policies` | 政策检索 | `policies` 页面权限 |
| `GET /policies/{id}` | 政策详情 | `policies` 页面权限 |
| `GET/POST/PATCH /collection-rules` | 采集规则管理 | 管理员 |
| `GET/POST/PATCH /schedules` | 计划管理 | 管理员 |
| `GET/POST /tasks` | 列表或手工创建任务 | 查看需任务权限，创建需管理员 |
| `GET /tasks/{id}` | 任务详情 | 任务权限 |
| `POST /tasks/{id}/cancel` | 请求取消 | 管理员 |
| `GET /tasks/{id}/logs` | 分页读取日志 | 任务权限 |
| `GET/POST/PATCH /users` | 用户与权限管理 | 管理员 |
| `GET /settings/effective` | 脱敏后的生效配置 | 管理员 |
| `GET /system/info` | 版本和运行信息 | 已登录 |
| `GET /health/live` | 存活检查 | 匿名 |
| `GET /health/ready` | 就绪检查 | 匿名，只返回摘要 |

列表 API 统一使用 `page`、`page_size`、`sort_by` 和 `sort_order`，`page_size` 设置可配置上限。

## 12. 日志与可观测性

- `src/logs/app.log` 保存当天应用日志；
- 历史日志按 `app.YYYY-MM-DD.log` 命名并按保留天数清理；
- `src/logs/server.pid` 由启停脚本维护；
- 日志包含 `request_id`、`task_id` 和结构化上下文；
- 任务业务日志同时写入数据库，供页面查询；
- `/health/live` 只检查进程；
- `/health/ready` 检查配置、SQLite 读写和后台执行器状态，不因 WebFetch 短暂不可用而使应用永久无法启动；
- WebFetch 连通状态在系统配置页单独展示。

`.gitignore` 必须忽略 `src/logs/`，不得忽略 `src/data/`。

## 13. 代码目录

```text
PolicyAnalysisSystem/
├── docs/
│   ├── requirements/
│   ├── design/
│   └── superpowers/specs/
├── deploy/
│   └── systemd/
├── src/
│   ├── app/
│   │   ├── backend/
│   │   └── frontend/
│   ├── config/
│   │   └── app.json
│   ├── data/
│   │   ├── app.sqlite3
│   │   └── password.txt
│   ├── JenkinsConfig/
│   │   └── Jenkinsfile
│   ├── logs/
│   └── tests/
│       ├── backend/
│       ├── frontend/
│       └── smoke/
├── README.md
├── start.sh
├── stop.sh
├── status.sh
├── start.ps1
├── stop.ps1
└── status.ps1
```

运行生成的 SQLite、密码文件和日志不随代码发布。种子清单作为后端资源随版本发布，初始化程序幂等导入数据库。

## 14. 测试策略

开发遵循测试驱动开发：先编写失败测试，再实现最小代码使测试通过，最后在测试保护下重构。

### 14.1 后端测试

pytest 覆盖：

- 密码文件解析、同步和原子写；
- 登录、退出、会话过期、CSRF 和登录限速；
- 管理员与普通用户 API 权限；
- 配置优先级、校验和脱敏；
- URL 规范化、允许域名和关键词过滤；
- 新旧新华网页面解析结果；
- 日期边界、音视频稿排除和正文判定；
- URL 去重、内容去重和正文修订；
- 任务状态流转、重试、取消、互斥与恢复；
- 政策筛选、排序、分页和 FTS5 检索；
- 健康检查和版本信息。

API 集成测试使用临时 SQLite、临时密码文件和 Mock WebFetch，不访问真实网站。核心后端语句覆盖率不得低于 80%。

### 14.2 前端测试

Vitest 与 Vue Test Utils 覆盖：

- 登录状态和错误提示；
- 权限菜单与受限路由；
- 政策筛选、分页和详情；
- 任务状态、统计、日志和刷新；
- 配置脱敏展示；
- 当前用户、退出和版本号。

浏览器冒烟测试覆盖登录、政策检索、手工任务创建和退出主路径。

### 14.3 静态检查与构建

交付前至少执行：

- Python 格式和静态检查；
- pytest 及覆盖率门槛；
- TypeScript 类型检查；
- ESLint；
- 前端单元测试；
- Vue 生产构建；
- 本地 API 与 SPA 冒烟测试。

## 15. 启停与部署

### 15.1 本地脚本

仓库根目录提供 Windows 和 Linux 的启动、停止、状态脚本。脚本从项目相对路径定位配置、数据和日志，不写绝对路径。启动成功后写入 `src/logs/server.pid`，状态脚本同时检查 PID 和健康接口。

### 15.2 systemd

Linux 部署提供一个 systemd 单元：

- 使用独立低权限服务用户；
- `WorkingDirectory`、环境文件、监听地址和端口由安装模板或环境配置提供；
- 异常退出自动重启；
- 启停和状态检查与仓库脚本兼容；
- 生产命令固定为单进程，确保调度器唯一。

### 15.3 Jenkins

`src/JenkinsConfig/Jenkinsfile` 使用 `Pipeline script from SCM`，Git 仓库使用 SSH 地址，并每 30 分钟轮询 SCM。流水线阶段：

1. 检出代码；
2. 安装或复用后端和前端依赖；
3. 执行全部静态检查、单元测试和构建；
4. 停止现有服务；
5. 增量同步到可配置的 `/opt` 子目录；
6. 首次部署时创建 `src/data`、`password.txt` 和 `app.json`；
7. 增量部署时保留现有 `src/data`、`src/config/app.json` 和 `src/logs`；
8. 启动服务；
9. 检查 `/health/ready`，失败时标记构建失败并保留诊断日志。

目标部署目录为 `/opt/PolicyAnalysisSystem`，但 Jenkinsfile 通过环境变量或参数读取，不把绝对路径写入业务代码。目标监听端口为 `30080`，通过 `app.json` 配置。

完成自测后，提交并推送 GitHub，手工触发一次 Jenkins 构建。验收访问地址暂定为 `http://192.168.0.111:30080`；若后续分配域名，只修改部署配置和反向代理。

## 16. 文档交付

实现过程中同步维护：

- 需求规格说明书；
- 设计说明书；
- README；
- 配置字段说明；
- 部署和运维说明；
- Jenkinsfile 与 systemd 说明。

README 至少包含系统介绍、页面介绍、配置说明、本地开发、部署方式、运维方式和访问方式。

## 17. 后续扩展点

首期边界不实现后续功能，但预留以下稳定接口：

- 新来源通过 `collector adapter` 接口接入；
- 推送规则引用政策类别、发布部门和来源；
- 分析模块读取标准化政策正文和发布时间，不依赖采集器内部结构；
- 大模型供应商通过独立客户端接口接入，密钥继续使用环境变量；
- 当 SQLite 的单机写入、数据规模或多实例部署成为实际瓶颈时，再提交迁移 PostgreSQL 的评估和审批。

## 18. 已确认决策摘要

| 决策 | 结果 |
| --- | --- |
| 首期范围 | 纵向 MVP，推送和分析仅占位 |
| 历史范围 | 任务执行时向前滚动 5 年 |
| 采集方案 | 官方入口增量 + 受控历史回填 |
| 技术栈 | FastAPI + Vue 3 + SQLite |
| 网页抓取 | 统一调用 WebFetch |
| 任务执行 | 单进程调度器 + 受控线程池 |
| 部署端口 | 通过配置使用 `30080` |
| 生产部署 | systemd + Jenkins，保留配置和数据 |
