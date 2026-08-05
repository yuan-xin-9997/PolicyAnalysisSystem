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
  created_at: '2026-07-31T04:00:00Z',
  updated_at: '2026-07-31T04:00:00Z',
}

function renderRules(role: 'admin' | 'user' = 'admin') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 1, username: role, role, pages: ['tasks'] }
  return render(RuleListView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
}

describe('采集规则', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('新增规则时清理空白和重复词，并提交完整 JSON', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/collection-rules') && options?.method === 'POST') return Promise.resolve(jsonResponse(rule, 201))
      if (path.endsWith('/collection-rules')) return Promise.resolve(jsonResponse([]))
      if (path.endsWith('/sources')) return Promise.resolve(jsonResponse([source]))
      return Promise.resolve(jsonResponse([category]))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRules()
    await screen.findByText('新增采集规则')
    await fireEvent.update(screen.getByLabelText('规则名称'), '中央政策')
    await fireEvent.update(screen.getByLabelText('来源'), 'xinhua')
    await fireEvent.update(screen.getByLabelText('类别'), 'politics')
    await fireEvent.update(screen.getByLabelText('包含词'), ' 政治局, 政治局, 国务院 ')
    await fireEvent.update(screen.getByLabelText('排除词'), ' 图片，视频 ')
    await fireEvent.update(screen.getByLabelText('RSS URL'), 'https://www.news.cn/rss.xml')
    await fireEvent.update(screen.getByLabelText('历史窗口'), '5')
    await fireEvent.click(screen.getByRole('button', { name: '保存规则' }))

    await waitFor(() => expect(screen.getByText('中央政策')).toBeInTheDocument())
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
    })
  })

  it('普通用户只读，不能看到新增表单', async () => {
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
  })
})
