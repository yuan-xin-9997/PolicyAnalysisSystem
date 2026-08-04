import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../../app/frontend/src/stores/auth'
import ScheduleListView from '../../app/frontend/src/views/tasks/ScheduleListView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const rule = {
  id: 9,
  name: '中央政策',
  source: { id: 1, code: 'xinhua', name: '新华网', organization: '新华社', base_url: 'https://www.news.cn', adapter_type: 'xinhua', allowed_domains: ['news.cn'], is_active: true },
  category: { id: 1, code: 'politics', name: '政治', description: null, is_active: true },
  include_keywords: ['政治局'],
  exclude_keywords: [],
  history_years: 5,
  discovery: { rss_urls: ['https://www.news.cn/rss.xml'], channel_urls: [] },
  is_active: true,
  created_at: '2026-07-31T04:00:00Z',
  updated_at: '2026-07-31T04:00:00Z',
}

function renderSchedules() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 1, username: 'admin', role: 'admin', pages: [] }
  return render(ScheduleListView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
}

describe('定时计划', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('无效 Cron 在客户端提示且不请求创建接口', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => {
      if (path.endsWith('/schedules')) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse([rule]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderSchedules()
    await screen.findByText('新增定时计划')
    await fireEvent.update(screen.getByLabelText('规则'), '9')
    await fireEvent.update(screen.getByLabelText('Cron 表达式'), '0 9 * *')
    await fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Cron 必须是北京时间 5 段表达式。')
    expect(fetchMock.mock.calls.some(([path, options]) => path.endsWith('/schedules') && options?.method === 'POST')).toBe(false)
  })

  it('创建计划默认停用，并显示后端返回的下次执行时间', async () => {
    const schedule = { id: 3, rule_id: 9, rule_name: '中央政策', cron_expression: '0 9 * * *', timezone: 'Asia/Shanghai', is_active: false, next_run_at: '2026-08-01T01:00:00Z', last_run_at: null }
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/schedules') && options?.method === 'POST') return Promise.resolve(jsonResponse(schedule, 201))
      if (path.endsWith('/schedules')) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse([rule]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderSchedules()
    await screen.findByText('新增定时计划')
    await fireEvent.update(screen.getByLabelText('规则'), '9')
    await fireEvent.update(screen.getByLabelText('Cron 表达式'), '0 9 * * *')
    await fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => expect(screen.getByText('2026-08-01 09:00:00')).toBeInTheDocument())
    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/schedules') && options?.method === 'POST')?.[1]?.body as string)
    expect(body).toEqual({ rule_id: 9, cron_expression: '0 9 * * *' })
  })
})
