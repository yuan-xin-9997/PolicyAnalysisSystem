import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../../app/frontend/src/stores/auth'
import RuleListView from '../../app/frontend/src/views/tasks/RuleListView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const source = {
  id: 1,
  code: 'xinhua',
  name: '新华网',
  organization: '新华社',
  base_url: 'https://www.news.cn',
  adapter_type: 'xinhua',
  allowed_domains: ['news.cn'],
  is_active: true,
}
const category = { id: 1, code: 'politics', name: '政治', description: null, is_active: true }
const rule = {
  id: 9,
  name: '中央政策',
  source,
  category,
  include_keywords: ['政治局'],
  exclude_keywords: ['图片'],
  history_years: 5,
  discovery: { rss_urls: ['https://www.news.cn/rss.xml'], channel_urls: [] },
  is_active: true,
  trigger_mode: 'manual',
  cron_expression: null,
  schedule_timezone: 'Asia/Shanghai',
  schedule_enabled: false,
  next_run_at: null,
  last_run_at: null,
  created_at: '2026-07-31T04:00:00Z',
  updated_at: '2026-07-31T04:00:00Z',
}

function renderRules(role: 'admin' | 'user' = 'admin') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 1, username: role, role, pages: ['tasks'] }
  return render(RuleListView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
}

function fillBaseFields() {
  fireEvent.update(screen.getByLabelText('规则名称'), '中央政策')
  fireEvent.update(screen.getByLabelText('来源'), 'xinhua')
  fireEvent.update(screen.getByLabelText('类别'), 'politics')
  fireEvent.update(screen.getByLabelText('包含词'), ' 政治局, 政治局, 国务院 ')
  fireEvent.update(screen.getByLabelText('排除词'), ' 图片，视频 ')
  fireEvent.update(screen.getByLabelText('RSS URL'), 'https://www.news.cn/rss.xml')
  fireEvent.update(screen.getByLabelText('历史窗口'), '5')
}

describe('采集规则', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('新增手工规则时清理空白和重复词，并提交完整 JSON', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/collection-rules') && options?.method === 'POST') return Promise.resolve(jsonResponse(rule, 201))
      if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([]))
      if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
      return Promise.resolve(jsonResponse([category]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRules()
    await screen.findByText('新增采集规则')
    fillBaseFields()
    await fireEvent.click(screen.getByRole('button', { name: '保存规则' }))

    await waitFor(() => expect(screen.getByText('手工触发')).toBeInTheDocument())
    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/collection-rules') && options?.method === 'POST')?.[1]?.body as string)
    expect(body).toMatchObject({
      name: '中央政策',
      source_code: 'xinhua',
      category_code: 'politics',
      include_keywords: ['政治局', '国务院'],
      exclude_keywords: ['图片', '视频'],
      history_years: 5,
      discovery: { rss_urls: ['https://www.news.cn/rss.xml'], channel_urls: [] },
      is_active: true,
      trigger_mode: 'manual',
    })
    expect(body).not.toHaveProperty('cron_expression')
    expect(body).not.toHaveProperty('schedule_enabled')
  })

  it('新增定时规则提交 Cron 配置，启用前弹出确认', async () => {
    const confirmMock = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmMock)
    const scheduledRule = { ...rule, trigger_mode: 'schedule', cron_expression: '0 9 * * *', schedule_enabled: true, next_run_at: '2026-08-01T01:00:00Z' }
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/collection-rules') && options?.method === 'POST') return Promise.resolve(jsonResponse(scheduledRule, 201))
      if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([]))
      if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
      return Promise.resolve(jsonResponse([category]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRules()
    await screen.findByText('新增采集规则')
    fillBaseFields()
    await fireEvent.click(screen.getByRole('radio', { name: '定时运行（按 Cron 自动执行，也可手工触发）' }))
    await fireEvent.update(screen.getByLabelText('Cron 表达式（北京时间 5 段，例如 0 9 * * *）'), '0 9 * * *')
    await fireEvent.click(screen.getByLabelText('启用定时运行'))
    await fireEvent.click(screen.getByRole('button', { name: '保存规则' }))

    expect(confirmMock).toHaveBeenCalledWith('启用定时运行前请确认已完成 WebFetch 检查和手工回填。是否继续？')
    await waitFor(() => expect(screen.getByText(/0 9 \* \* \*（已启用）/)).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/2026-08-01 09:00:00/)).toBeInTheDocument())
    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/collection-rules') && options?.method === 'POST')?.[1]?.body as string)
    expect(body).toMatchObject({
      trigger_mode: 'schedule',
      cron_expression: '0 9 * * *',
      schedule_enabled: true,
    })
  })

  it('定时规则无效 Cron 在客户端提示且不请求创建接口', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => {
      if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([]))
      if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
      return Promise.resolve(jsonResponse([category]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRules()
    await screen.findByText('新增采集规则')
    fillBaseFields()
    await fireEvent.click(screen.getByRole('radio', { name: '定时运行（按 Cron 自动执行，也可手工触发）' }))
    await fireEvent.update(screen.getByLabelText('Cron 表达式（北京时间 5 段，例如 0 9 * * *）'), '0 9 * *')
    await fireEvent.click(screen.getByRole('button', { name: '保存规则' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('定时运行的 Cron 必须是北京时间 5 段表达式。')
    expect(fetchMock.mock.calls.some(([path, options]) => path.endsWith('/collection-rules') && options?.method === 'POST')).toBe(false)
  })

  it('编辑规则预填表单并通过 PATCH 保存', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/collection-rules/9') && options?.method === 'PATCH') return Promise.resolve(jsonResponse({ ...rule, name: '更新后的规则' }))
      if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([rule]))
      if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
      return Promise.resolve(jsonResponse([category]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRules()
    await screen.findByText('新增采集规则')
    await fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    expect(await screen.findByText('编辑采集规则 #9')).toBeInTheDocument()
    const nameInput = screen.getByLabelText('规则名称') as HTMLInputElement
    expect(nameInput.value).toBe('中央政策')
    await fireEvent.update(nameInput, '更新后的规则')
    await fireEvent.click(screen.getByRole('button', { name: '保存规则' }))

    await waitFor(() => expect(screen.getByText('更新后的规则')).toBeInTheDocument())
    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/collection-rules/9') && options?.method === 'PATCH')?.[1]?.body as string)
    expect(body).toMatchObject({ name: '更新后的规则', trigger_mode: 'manual' })
    expect(screen.getByText('新增采集规则')).toBeInTheDocument()
  })

  it('普通用户只读，不能看到新增表单与编辑按钮', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path: string) => {
        if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([rule]))
        if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
        return Promise.resolve(jsonResponse([category]))
      }),
    )

    renderRules('user')
    expect(await screen.findByText('普通用户仅可查看规则，不能新增或编辑。')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'ID' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '9' })).toBeInTheDocument()
    expect(screen.queryByText('新增采集规则')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
  })
})
