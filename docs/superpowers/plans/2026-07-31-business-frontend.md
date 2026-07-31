# 业务前端实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成政策数据库、任务中心、采集规则、定时计划、权限管理和系统配置的可用 Vue 界面，并通过组件与浏览器冒烟测试验证主要路径。

**架构：** Vue Router 负责页面权限，Pinia 保存认证与页面状态，统一 API 客户端处理会话、CSRF 和错误。Element Plus 提供表格、表单、分页和状态组件，后端继续作为唯一授权边界。

**技术栈：** Vue 3、TypeScript、Pinia、Vue Router、Element Plus、Vitest、Vue Test Utils、Testing Library、Playwright。

---

## 实施前提

- 先依次执行平台基础与采集后端两份计划。
- 所有时间通过统一函数转换为 `Asia/Shanghai`，不得在组件内各自拼接时区。
- 正文只使用文本节点或 `textContent` 渲染，禁止 `v-html`。
- 网络测试只替换 `fetch` 边界并返回完整 API 数据结构，不 mock 被测 store 或组件。

## 文件结构与职责

| 文件或目录 | 职责 |
| --- | --- |
| `src/app/frontend/src/api/client.ts` | 请求、CSRF、错误解码和分页参数 |
| `src/app/frontend/src/api/types.ts` | 与后端响应一致的 TypeScript 类型 |
| `src/app/frontend/src/utils/time.ts` | 北京时间格式化 |
| `src/app/frontend/src/components/StatusTag.vue` | 任务状态中文映射与颜色 |
| `src/app/frontend/src/views/policies/` | 政策筛选、列表和详情 |
| `src/app/frontend/src/views/tasks/` | 规则、计划、任务列表、详情和日志 |
| `src/app/frontend/src/views/admin/` | 用户权限和配置展示 |
| `src/app/frontend/e2e/` | Playwright 主路径冒烟测试 |

### 任务 1：固定 API 类型、错误处理和北京时间

**文件：**
- 创建：`src/app/frontend/src/api/types.ts`
- 修改：`src/app/frontend/src/api/client.ts`
- 创建：`src/app/frontend/src/utils/time.ts`
- 测试：`src/tests/frontend/api-client.spec.ts`
- 测试：`src/tests/frontend/time.spec.ts`

- [ ] **步骤 1：编写完整响应和时区测试**

```typescript
// src/tests/frontend/time.spec.ts
import { describe, expect, it } from 'vitest'
import { formatBeijingTime } from '../../app/frontend/src/utils/time'

describe('formatBeijingTime', () => {
  it('把 UTC 时间统一显示为北京时间', () => {
    expect(formatBeijingTime('2026-07-31T04:30:00Z')).toBe('2026-07-31 12:30:00')
  })
})
```

`api-client.spec.ts` 替换全局 fetch，返回完整的成功分页响应和设计中的完整错误响应。验证查询数组不丢失、状态变更添加 CSRF、401 清理 auth store、错误保留 `code` 和 `request_id`。

- [ ] **步骤 2：运行并确认失败**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/time.spec.ts src/tests/frontend/api-client.spec.ts`

预期：FAIL，缺少 `formatBeijingTime` 或请求类型。

- [ ] **步骤 3：实现类型和纯函数**

```typescript
// src/app/frontend/src/utils/time.ts
const beijingFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
})

export function formatBeijingTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return beijingFormatter.format(date).replaceAll('/', '-').replace(',', '')
}
```

`types.ts` 定义 `ApiErrorBody`、`Page<T>`、`CurrentUser`、`PolicySummary`、`PolicyDetail`、`CrawlTask`、`CrawlTaskItem`、`TaskLog`、`CollectionRule`、`Schedule` 和 `EffectiveSettings`，字段名逐项匹配后端 schema。

- [ ] **步骤 4：验证**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/time.spec.ts src/tests/frontend/api-client.spec.ts`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend/src/api src/app/frontend/src/utils src/tests/frontend/api-client.spec.ts src/tests/frontend/time.spec.ts
git commit -m "feat(前端): 统一 API 类型与北京时间显示"
```

### 任务 2：实现政策筛选、列表和详情

**文件：**
- 创建：`src/app/frontend/src/views/policies/PolicyListView.vue`
- 创建：`src/app/frontend/src/views/policies/PolicyDetailView.vue`
- 创建：`src/app/frontend/src/views/policies/policy-query.ts`
- 修改：`src/app/frontend/src/router/index.ts`
- 测试：`src/tests/frontend/policy-list.spec.ts`
- 测试：`src/tests/frontend/policy-detail.spec.ts`

- [ ] **步骤 1：编写用户可见行为测试**

```typescript
// src/tests/frontend/policy-detail.spec.ts
import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import PolicyDetailView from '../../app/frontend/src/views/policies/PolicyDetailView.vue'

describe('政策详情', () => {
  it('以纯文本显示正文和来源元数据', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 7,
      title: '中共中央政治局召开会议',
      content_text: '<script>window.hacked=true</script>政策正文',
      canonical_url: 'https://www.news.cn/example/c.html',
      publisher: '新华网',
      category: { code: 'politburo_meeting', name: '中央政治局会议' },
      source: { code: 'xinhua', name: '新华网' },
      published_at: '2026-07-30T06:00:00Z',
      first_crawled_at: '2026-07-31T04:00:00Z',
      last_crawled_at: '2026-07-31T04:00:00Z',
      content_hash: 'abc123',
      latest_task_id: 9,
    }), { status: 200 })))
    render(PolicyDetailView, { props: { policyId: 7 } })
    expect(await screen.findByText('<script>window.hacked=true</script>政策正文')).toBeTruthy()
    expect(document.querySelector('script')).toBeNull()
  })
})
```

列表测试覆盖 6 类筛选、清空筛选、分页、排序、空状态和 API 错误。测试断言用户看到的表头、筛选值和行内容，不断言 Element Plus 内部实现。

- [ ] **步骤 2：运行并确认组件缺失失败**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/policy-list.spec.ts src/tests/frontend/policy-detail.spec.ts`

预期：FAIL，无法导入政策页面。

- [ ] **步骤 3：实现页面和 URL 查询同步**

`policy-query.ts` 负责将表单状态转换为后端查询参数，并从路由 query 恢复。空值不得发送。列表列为标题、发布部门、类别、发布时间、最近抓取时间和来源；点击标题进入详情。

详情正文使用 `<pre class="policy-content">{{ policy.content_text }}</pre>`。来源链接使用 `target="_blank"` 和 `rel="noopener noreferrer"`。

- [ ] **步骤 4：验证组件和类型检查**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/policy-list.spec.ts src/tests/frontend/policy-detail.spec.ts`

运行：`npm --prefix src/app/frontend run type-check`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend/src/views/policies src/app/frontend/src/router/index.ts src/tests/frontend/policy-list.spec.ts src/tests/frontend/policy-detail.spec.ts
git commit -m "feat(政策页面): 添加组合检索与正文详情"
```

### 任务 3：实现任务列表、详情、日志和轮询

**文件：**
- 创建：`src/app/frontend/src/components/StatusTag.vue`
- 创建：`src/app/frontend/src/views/tasks/TaskListView.vue`
- 创建：`src/app/frontend/src/views/tasks/TaskDetailView.vue`
- 创建：`src/app/frontend/src/views/tasks/use-task-polling.ts`
- 修改：`src/app/frontend/src/router/index.ts`
- 测试：`src/tests/frontend/task-list.spec.ts`
- 测试：`src/tests/frontend/task-detail.spec.ts`
- 测试：`src/tests/frontend/task-polling.spec.ts`

- [ ] **步骤 1：编写任务状态和轮询生命周期测试**

```typescript
// src/tests/frontend/task-polling.spec.ts
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createTaskPolling } from '../../app/frontend/src/views/tasks/use-task-polling'

describe('任务轮询', () => {
  beforeEach(() => vi.useFakeTimers())

  it('运行态每 2 秒刷新，进入终态后停止', async () => {
    const load = vi.fn()
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({ status: 'succeeded' })
    const polling = createTaskPolling(load, 2000)
    await polling.start()
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(4000)
    expect(load).toHaveBeenCalledTimes(2)
    polling.stop()
  })
})
```

详情测试覆盖 5 个统计数、进度、明细 reason、分级日志、取消按钮权限和终态隐藏取消按钮。

- [ ] **步骤 2：运行并确认红灯**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/task-list.spec.ts src/tests/frontend/task-detail.spec.ts src/tests/frontend/task-polling.spec.ts`

预期：FAIL，缺少组件或 polling 工厂。

- [ ] **步骤 3：实现状态映射与轮询**

`StatusTag` 映射：等待、运行、成功、部分成功、失败、已取消。`createTaskPolling` 只接受加载函数和间隔，返回 `start/stop`；组件卸载必须调用 `stop`。

任务列表筛选状态、规则、触发方式和起止时间。详情并行读取任务、items 和 logs；日志表显示北京时间、级别和脱敏消息，不渲染 `context_json` 中未知 HTML。

- [ ] **步骤 4：验证任务页面**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/task-list.spec.ts src/tests/frontend/task-detail.spec.ts src/tests/frontend/task-polling.spec.ts`

预期：全部 PASS，Vitest 没有未清理 timer 提示。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend/src/components/StatusTag.vue src/app/frontend/src/views/tasks src/app/frontend/src/router/index.ts src/tests/frontend/task-list.spec.ts src/tests/frontend/task-detail.spec.ts src/tests/frontend/task-polling.spec.ts
git commit -m "feat(任务页面): 添加进度日志与状态轮询"
```

### 任务 4：实现采集规则、计划和手工触发

**文件：**
- 创建：`src/app/frontend/src/views/tasks/RuleListView.vue`
- 创建：`src/app/frontend/src/views/tasks/RuleFormDialog.vue`
- 创建：`src/app/frontend/src/views/tasks/ScheduleListView.vue`
- 创建：`src/app/frontend/src/views/tasks/ScheduleFormDialog.vue`
- 修改：`src/app/frontend/src/views/tasks/TaskListView.vue`
- 测试：`src/tests/frontend/rules.spec.ts`
- 测试：`src/tests/frontend/schedules.spec.ts`
- 测试：`src/tests/frontend/manual-task.spec.ts`

- [ ] **步骤 1：编写表单验证和手工触发测试**

规则测试输入来源、类别、包含词、排除词和 `history_years=5`，断言提交完整 JSON。计划测试断言无效 Cron 在客户端提示且不请求 API，创建计划时 `is_active=false`。手工触发测试断言管理员确认后 `POST /api/v1/tasks`，成功后跳转任务详情。

- [ ] **步骤 2：运行并确认红灯**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/rules.spec.ts src/tests/frontend/schedules.spec.ts src/tests/frontend/manual-task.spec.ts`

预期：FAIL，缺少表单组件。

- [ ] **步骤 3：实现规则与计划 UI**

规则关键词使用可增删标签输入，保存前去空白和重复。历史窗口限制为 1–20，首期默认 5。Cron 表单明确提示为北京时间 5 段表达式，并显示后端返回的下次执行时间。启用计划前弹窗提示先完成 WebFetch 检查和手工回填。

普通用户可查看任务、规则和计划，但看不到新增、编辑、启停、手工触发和取消入口；后端仍负责拒绝越权请求。

- [ ] **步骤 4：验证**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/rules.spec.ts src/tests/frontend/schedules.spec.ts src/tests/frontend/manual-task.spec.ts`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend/src/views/tasks src/tests/frontend/rules.spec.ts src/tests/frontend/schedules.spec.ts src/tests/frontend/manual-task.spec.ts
git commit -m "feat(采集配置): 添加规则计划与手工触发界面"
```

### 任务 5：完成用户权限、配置和占位页面

**文件：**
- 修改：`src/app/frontend/src/views/UsersView.vue`
- 修改：`src/app/frontend/src/views/SettingsView.vue`
- 修改：`src/app/frontend/src/views/PlaceholderView.vue`
- 修改：`src/app/frontend/src/layouts/AppLayout.vue`
- 测试：`src/tests/frontend/users.spec.ts`
- 测试：`src/tests/frontend/settings.spec.ts`
- 测试：`src/tests/frontend/placeholders.spec.ts`

- [ ] **步骤 1：编写安全和权限可见性测试**

用户测试覆盖新增、重置密码、角色、启停和页面授权，并断言页面从不显示现有密码。配置测试用完整嵌套响应断言密钥字段为 `********`，同时显示配置来源和 WebFetch 连通状态。占位页断言没有发送、分析或预测按钮。

- [ ] **步骤 2：运行并确认现有基础页不满足测试**

运行：`npm --prefix src/app/frontend run test -- --run src/tests/frontend/users.spec.ts src/tests/frontend/settings.spec.ts src/tests/frontend/placeholders.spec.ts`

预期：FAIL，基础页缺少完整交互或字段。

- [ ] **步骤 3：实现完整管理交互**

重置密码只提供两次新密码输入，成功后清空表单。管理员角色的页面权限显示为全选且不可取消。配置树按分组表格显示键、值和来源；任何键名含 `password`、`secret`、`token` 或 `api_key` 时，前端再做一层掩码防御。

推送管理文案说明后续按类别和部门推送邮件；政策分析文案说明后续词频、表述差异和大模型预测。两页只读。

- [ ] **步骤 4：运行全部前端单元测试**

运行：`npm --prefix src/app/frontend run test -- --run`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend/src/views src/app/frontend/src/layouts src/tests/frontend
git commit -m "feat(管理页面): 完善权限配置与功能占位页"
```

### 任务 6：增加浏览器冒烟与生产构建验证

**文件：**
- 修改：`src/app/frontend/package.json`
- 创建：`src/app/frontend/playwright.config.ts`
- 创建：`src/app/frontend/e2e/platform.spec.ts`
- 创建：`src/app/frontend/e2e/policy-task.spec.ts`
- 修改：`src/app/frontend/src/styles/main.css`
- 修改：`README.md`

- [ ] **步骤 1：安装 Playwright 并先写主路径测试**

运行：`npm --prefix src/app/frontend install -D @playwright/test`

```typescript
// src/app/frontend/e2e/platform.spec.ts
import { expect, test } from '@playwright/test'

test('管理员登录后可见全部导航并能退出', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByText('政策数据库')).toBeVisible()
  await expect(page.getByText('权限管理')).toBeVisible()
  await expect(page.getByText(/^v0\./)).toBeVisible()
  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(/\/login$/)
})
```

`policy-task.spec.ts` 使用后端测试种子启动应用，验证政策筛选、详情纯文本、手工任务、任务详情和重复任务结果。

- [ ] **步骤 2：运行并确认浏览器测试暴露未接通路径**

运行：`npm --prefix src/app/frontend run test:e2e`

预期：若测试服务器、路由或可访问标签未接通则 FAIL；不要先改测试选择器绕过可访问性问题。

- [ ] **步骤 3：补齐可访问性、样式和测试启动脚本**

Playwright 配置启动临时 FastAPI 测试服务，测试数据放在临时目录。CSS 完成桌面管理布局、固定侧栏底部用户区、表格溢出、窄屏折叠菜单、正文排版和错误状态；不引入额外 UI 框架。

- [ ] **步骤 4：执行前端完整验证**

运行：`npm --prefix src/app/frontend run type-check`

运行：`npm --prefix src/app/frontend run lint`

运行：`npm --prefix src/app/frontend run test -- --run`

运行：`npm --prefix src/app/frontend run build`

运行：`npm --prefix src/app/frontend run test:e2e`

预期：5 条命令全部退出码为 0；浏览器测试无失败、重试或 console error。

- [ ] **步骤 5：提交**

```bash
git add src/app/frontend README.md
git commit -m "test(前端): 添加业务主路径浏览器冒烟"
```

