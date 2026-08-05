import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../../app/frontend/src/stores/auth'
import TaskDetailView from '../../app/frontend/src/views/tasks/TaskDetailView.vue'
import TaskListView from '../../app/frontend/src/views/tasks/TaskListView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('手工触发采集', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('管理员确认后创建任务并跳转详情', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore().user = { id: 1, username: 'admin', role: 'admin', pages: [] }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/tasks', name: 'tasks', component: TaskListView },
        { path: '/tasks/:taskId', name: 'task-detail', component: TaskDetailView },
      ],
    })
    await router.push('/tasks')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/tasks') && options?.method === 'POST') {
        return Promise.resolve(jsonResponse({ id: 42, rule_id: 9, trigger_type: 'manual', status: 'pending', requested_by: 1, scheduled_for: null, started_at: null, finished_at: null, cancel_requested_at: null, error_summary: null, progress: { processed: 0, discovered: 0 }, counts: { success: 0, duplicate: 0, filtered: 0, failed: 0, total_terminal_items: 0 } }, 201))
      }
      if (path.endsWith('/collection-rules')) {
        return Promise.resolve(
          jsonResponse([
            {
              id: 9,
              name: '中央政治局会议',
              source: { id: 1, code: 'xinhua', name: '新华网', organization: '新华社', base_url: 'https://news.cn/', adapter_type: 'xinhua', allowed_domains: ['news.cn'], is_active: true },
              category: { id: 1, code: 'politburo_meeting', name: '中央政治局会议', description: null, is_active: true },
              include_keywords: ['中共中央政治局召开会议'],
              exclude_keywords: ['视频'],
              history_years: 5,
              discovery: { rss_urls: [], channel_urls: ['https://www.news.cn/politics/leaders/index.htm'] },
              is_active: true,
              created_at: '2026-07-31T04:00:00Z',
              updated_at: '2026-07-31T04:00:00Z',
            },
          ]),
        )
      }
      return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(TaskListView, { global: { plugins: [pinia, router] } })
    await screen.findByText('手工触发')
    await screen.findByRole('option', { name: '#9 中央政治局会议' })
    await fireEvent.update(screen.getByLabelText('采集规则'), '9')
    await fireEvent.click(screen.getByRole('button', { name: '触发采集' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/tasks/42'))
    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')?.[1]?.body as string)
    expect(body).toEqual({ rule_id: 9 })
  })
})
