import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TaskDetailView from '../../app/frontend/src/views/tasks/TaskDetailView.vue'
import TaskListView from '../../app/frontend/src/views/tasks/TaskListView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function taskPage(overrides: object = {}) {
  return {
    items: [
      {
        id: 9,
        rule_id: 3,
        trigger_type: 'manual',
        status: 'running',
        requested_by: 1,
        scheduled_for: null,
        started_at: '2026-07-31T04:00:00Z',
        finished_at: null,
        cancel_requested_at: null,
        error_summary: null,
        progress: { processed: 2, discovered: 5 },
        counts: { success: 1, duplicate: 1, filtered: 0, failed: 0, total_terminal_items: 2 },
      },
    ],
    total: 21,
    page: 1,
    page_size: 20,
    ...overrides,
  }
}

async function renderList() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/tasks', name: 'tasks', component: TaskListView },
      { path: '/tasks/:taskId', name: 'task-detail', component: TaskDetailView },
    ],
  })
  await router.push('/tasks')
  return render(TaskListView, { global: { plugins: [router] } })
}

describe('任务列表', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('展示状态、筛选、分页和详情入口', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(taskPage())))
    vi.stubGlobal('fetch', fetchMock)

    await renderList()

    expect(await screen.findByText('#9')).toBeInTheDocument()
    expect(screen.getAllByText('运行').length).toBeGreaterThan(0)
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
    expect(screen.getByText('2026-07-31 12:00:00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '#9' })).toHaveAttribute('href', '/tasks/9')

    await fireEvent.update(screen.getByLabelText('状态'), 'running')
    await fireEvent.update(screen.getByLabelText('规则 ID'), '3')
    await fireEvent.update(screen.getByLabelText('触发方式'), 'manual')
    await fireEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('status=running&rule_id=3&trigger_type=manual'))

    await fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('page=2'))
  })

  it('展示空状态和 API 错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(taskPage({ items: [], total: 0 }))))
    await renderList()
    expect(await screen.findByText('暂无任务')).toBeInTheDocument()

    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: 'TASK_ERROR', message: '任务不可用。', request_id: 'id', details: {} } }, 503),
      ),
    )
    await renderList()
    expect(await screen.findByRole('alert')).toHaveTextContent('任务不可用。')
  })
})
