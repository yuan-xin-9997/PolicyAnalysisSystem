# 移动端响应式适配实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让政策分析系统在 320px 及以上手机、平板和桌面视口中均可完成主要操作，并以移动顶部栏和抽屉替代窄屏常驻侧栏。

**架构：** 保留现有 Vue 页面和路由，在 `AppLayout.vue` 中增加仅属于布局的移动菜单状态与关闭事件；各页面只补充必要的滚动容器语义，统一响应行为集中在 `main.css`。测试使用 Vitest 验证交互和 DOM 结构，使用 Playwright 在 320px 与 390px 视口验证无页面级溢出和关键流程。

**技术栈：** Vue 3、Vue Router、Pinia、TypeScript、CSS 媒体查询、Vitest、Testing Library、Playwright

---

## 文件结构

- 修改 `src/app/frontend/src/layouts/AppLayout.vue`：移动顶部栏、抽屉、遮罩及开关状态。
- 修改 `src/app/frontend/src/styles/main.css`：桌面/平板/手机断点、触控尺寸、安全区、局部横向滚动和图表适配。
- 修改政策、规则、任务与分析视图：为业务表格增加局部滚动容器。
- 修改对应 Vitest 测试：验证移动菜单和表格容器结构。
- 创建 `src/app/frontend/e2e/mobile.spec.ts`：在真实窄视口验证导航、布局溢出和关键页面操作。
- 修改 `README.md`：补充移动端访问与兼容范围。

### 任务 1：移动导航交互

**文件：**
- 修改：`src/tests/frontend/navigation.spec.ts`
- 修改：`src/app/frontend/src/layouts/AppLayout.vue`
- 修改：`src/app/frontend/src/styles/main.css`

- [ ] **步骤 1：编写失败的移动菜单测试**

在 `navigation.spec.ts` 中挂载 `AppLayout`，断言菜单按钮初始具有 `aria-expanded="false"`；点击后变为 `true` 并出现遮罩；触发 Escape 后恢复为 `false`；再次打开并点击导航链接后关闭。

```ts
it('opens and closes the mobile navigation accessibly', async () => {
  const { getByRole, queryByTestId } = renderLayout()
  const toggle = getByRole('button', { name: '打开导航菜单' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(queryByTestId('navigation-backdrop')).toBeInTheDocument()
  await fireEvent.keyDown(window, { key: 'Escape' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd src/app/frontend && npm test -- --run ../../tests/frontend/navigation.spec.ts`

预期：FAIL，找不到名称为“打开导航菜单”的按钮。

- [ ] **步骤 3：实现最少移动导航**

在 `AppLayout.vue` 新增 `mobileNavigationOpen` 与 open/close/toggle 方法，监听 Escape 和路由变化，并在卸载时清理 body 滚动状态。模板新增 `.mobile-header`、`.navigation-backdrop`，侧栏绑定 `.is-open`。按钮须有 `aria-controls`、`aria-expanded` 和动态 `aria-label`。

```ts
const mobileNavigationOpen = ref(false)
function closeMobileNavigation(): void {
  mobileNavigationOpen.value = false
}
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeMobileNavigation()
}
```

- [ ] **步骤 4：运行导航测试验证通过**

运行：`cd src/app/frontend && npm test -- --run ../../tests/frontend/navigation.spec.ts`

预期：该文件全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/app/frontend/src/layouts/AppLayout.vue src/app/frontend/src/styles/main.css src/tests/frontend/navigation.spec.ts
git commit -m "feat(前端): 添加移动端抽屉导航"
```

### 任务 2：业务表格局部滚动

**文件：**
- 修改：`src/tests/frontend/policy-list.spec.ts`
- 修改：`src/tests/frontend/task-list.spec.ts`
- 修改：`src/tests/frontend/task-detail.spec.ts`
- 修改：`src/tests/frontend/rules.spec.ts`
- 修改：`src/tests/frontend/analysis.spec.ts`
- 修改：`src/app/frontend/src/views/policies/PolicyListView.vue`
- 修改：`src/app/frontend/src/views/tasks/RuleListView.vue`
- 修改：`src/app/frontend/src/views/tasks/TaskListView.vue`
- 修改：`src/app/frontend/src/views/tasks/TaskDetailView.vue`
- 修改：`src/app/frontend/src/views/analysis/AnalysisView.vue`

- [ ] **步骤 1：编写失败的表格容器测试**

在各页面成功加载用例中增加断言，所有 `.data-table` 都具有最近的 `.table-wrap` 父级。

```ts
for (const table of document.querySelectorAll('.data-table')) {
  expect(table.parentElement).toHaveClass('table-wrap')
}
```

- [ ] **步骤 2：运行页面测试验证失败**

运行：

```bash
cd src/app/frontend
npm test -- --run ../../tests/frontend/policy-list.spec.ts ../../tests/frontend/task-list.spec.ts ../../tests/frontend/task-detail.spec.ts ../../tests/frontend/rules.spec.ts ../../tests/frontend/analysis.spec.ts
```

预期：FAIL，至少一个 `.data-table` 的父元素不含 `table-wrap`。

- [ ] **步骤 3：用滚动容器包裹表格**

对条件渲染保持原样，只在表格外新增容器。每个容器使用描述内容的 `aria-label`，不修改列、数据绑定或事件处理。

```vue
<div class="table-wrap" tabindex="0" aria-label="数据表格，可横向滚动">
  <table class="data-table">...</table>
</div>
```

- [ ] **步骤 4：运行页面测试验证通过**

运行任务 2 步骤 2 的命令，预期所有指定测试 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/app/frontend/src/views src/tests/frontend
git commit -m "fix(前端): 限制移动端表格横向滚动范围"
```

### 任务 3：全局窄屏布局与触控适配

**文件：**
- 修改：`src/app/frontend/src/styles/main.css`
- 创建：`src/app/frontend/e2e/mobile.spec.ts`

- [ ] **步骤 1：编写失败的窄屏端到端测试**

使用现有 mock API 登录，在 320px 和 390px 视口断言页面根元素无非预期横向溢出、移动菜单可打开、导航项可跳转。

```ts
for (const width of [320, 390]) {
  test(`mobile layout works at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 760 })
    await page.goto('/login')
    await page.getByLabel('用户名').fill('admin')
    await page.getByLabel('密码').fill('admin123')
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByRole('button', { name: '打开导航菜单' }).click()
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(1)
  })
}
```

- [ ] **步骤 2：运行端到端测试验证失败**

运行：`cd src/app/frontend && npx playwright test e2e/mobile.spec.ts`

预期：FAIL，移动菜单或无溢出断言未满足。

- [ ] **步骤 3：完成共享响应式样式**

在 `main.css` 中完成以下实现：

- 默认隐藏移动顶部栏和遮罩。
- 820px 以下显示顶部栏，将侧栏固定为屏外抽屉，通过 `.is-open` 移入。
- 520px 以下缩减页面留白，标题、操作栏、分页、分析工具栏和任务元数据改为单列或换行。
- 表格容器允许触控横向滚动，数据表保持可读最小宽度。
- 标签栏允许横向滚动，图表高度使用 `clamp()`，长文本安全断行。
- 登录页和顶部栏使用 `env(safe-area-inset-*)`。
- 输入和按钮保持至少 44px 触控高度。
- `prefers-reduced-motion: reduce` 时关闭抽屉动画。

- [ ] **步骤 4：运行窄屏端到端测试验证通过**

运行：`cd src/app/frontend && npx playwright test e2e/mobile.spec.ts`

预期：320px 与 390px 用例全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/app/frontend/src/styles/main.css src/app/frontend/e2e/mobile.spec.ts
git commit -m "feat(前端): 完善窄屏页面与触控适配"
```

### 任务 4：文档与完整回归

**文件：**
- 修改：`README.md`

- [ ] **步骤 1：更新文档**

在 README 的访问方式或页面说明中补充：支持 320px 及以上现代移动浏览器；移动端通过顶部菜单访问页面；宽表格在表格区域内左右滑动。

- [ ] **步骤 2：运行静态质量检查**

```bash
cd src/app/frontend
npm run type-check
npm run lint
npm run build
```

预期：三条命令退出码均为 0。

- [ ] **步骤 3：运行完整前端测试**

```bash
cd src/app/frontend
npm test -- --run
npx playwright test
```

预期：Vitest 和 Playwright 全部 PASS。

- [ ] **步骤 4：检查差异**

运行 `git diff --check` 和 `git status --short`。预期前者无输出，后者仅包含本计划范围内文件。

- [ ] **步骤 5：Commit**

```bash
git add README.md
git commit -m "docs: 补充移动端访问说明"
```

