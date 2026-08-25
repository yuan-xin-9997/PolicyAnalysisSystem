import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PolicyDetailView from '../../app/frontend/src/views/policies/PolicyDetailView.vue'
import PolicyListView from '../../app/frontend/src/views/policies/PolicyListView.vue'
import {
  defaultPolicyQuery,
  fromRouteQuery,
  toPolicyApiQuery,
} from '../../app/frontend/src/views/policies/policy-query'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function policyPage(overrides: object = {}) {
  return {
    items: [
      {
        id: 7,
        title: '中共中央政治局召开会议',
        canonical_url: 'https://news.cn/example/c.html',
        publisher: '新华社',
        category: { id: 1, code: 'politburo_meeting', name: '中央政治局会议' },
        source: { id: 1, code: 'xinhua', name: '新华网' },
        published_at: '2026-07-30T06:00:00Z',
        first_crawled_at: '2026-07-31T04:00:00Z',
        last_crawled_at: '2026-07-31T04:30:00Z',
        content_hash: 'hash',
        latest_task_id: 9,
      },
    ],
    total: 21,
    page: 1,
    page_size: 20,
    sort_by: 'published_at',
    sort_order: 'desc',
    ...overrides,
  }
}

function filterOptions() {
  return {
    publishers: ['中国政府网', '新华社'],
    categories: [
      { id: 2, code: 'economy', name: '经济工作' },
      { id: 1, code: 'politburo_meeting', name: '中央政治局会议' },
    ],
    sources: [
      { id: 2, code: 'government', name: '政府网' },
      { id: 1, code: 'xinhua', name: '新华网' },
    ],
  }
}

function successfulFetch() {
  return vi.fn().mockImplementation((input: string | URL | Request) => {
    const url = String(input)
    return Promise.resolve(jsonResponse(url.includes('/policies/filters') ? filterOptions() : policyPage()))
  })
}

async function renderList(path = '/policies?keyword=政治局&full_text=经济工作') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/policies', name: 'policies', component: PolicyListView },
      { path: '/policies/:policyId', name: 'policy-detail', component: PolicyDetailView },
    ],
  })
  await router.push(path)
  return { router, ...render(PolicyListView, { global: { plugins: [router] } }) }
}

function deferredResponse() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

describe('政策列表', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('序列化、恢复并清空独立的正文全文检索状态', () => {
    const restored = fromRouteQuery({
      keyword: '政治局',
      full_text: '科技创新',
      category_id: '2',
      sort_by: 'last_crawled_at',
      sort_order: 'asc',
      page: '3',
    })

    expect(restored.fullText).toBe('科技创新')
    expect(toPolicyApiQuery(restored)).toMatchObject({
      keyword: '政治局',
      full_text: '科技创新',
      category_id: '2',
      sort_by: 'last_crawled_at',
      sort_order: 'asc',
      page: 3,
    })
    expect(defaultPolicyQuery()).toMatchObject({
      fullText: '',
      sortBy: 'published_at',
      sortOrder: 'desc',
      page: 1,
    })
  })

  it('展示筛选、排序、分页和政策行', async () => {
    const fetchMock = successfulFetch()
    vi.stubGlobal('fetch', fetchMock)

    await renderList()

    expect(await screen.findByText('中共中央政治局召开会议')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '新华社' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '中央政治局会议' })).toBeInTheDocument()
    expect(screen.getByText('2026-07-30 14:00:00')).toBeInTheDocument()
    expect(screen.getByText('2026-07-31 12:30:00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '中共中央政治局召开会议' })).toHaveAttribute(
      'href',
      '/policies/7',
    )

    expect(await screen.findByRole('option', { name: '中国政府网' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '经济工作' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '政府网' })).toBeInTheDocument()
    await fireEvent.update(screen.getByLabelText('发布部门'), '新华社')
    await fireEvent.update(screen.getByLabelText('政策类别'), '1')
    await fireEvent.update(screen.getByLabelText('政策来源'), '1')
    await fireEvent.update(screen.getByLabelText('发布时间起'), '2026-07-01')
    await fireEvent.update(screen.getByLabelText('发布时间止'), '2026-07-31')
    await fireEvent.click(screen.getByRole('button', { name: '筛选' }))

    await waitFor(() =>
      expect(fetchMock.mock.calls.at(-1)?.[0]).toContain(
        'keyword=%E6%94%BF%E6%B2%BB%E5%B1%80&full_text=%E7%BB%8F%E6%B5%8E%E5%B7%A5%E4%BD%9C&publisher=%E6%96%B0%E5%8D%8E%E7%A4%BE&category_id=1&source_id=1&published_from=2026-07-01T00%3A00%3A00%2B08%3A00&published_to=2026-07-31T23%3A59%3A59%2B08%3A00',
      ),
    )

    expect(await screen.findByRole('columnheader', { name: /发布时间/ })).toHaveAttribute(
      'aria-sort',
      'descending',
    )
    expect(screen.getByText('降序（最新优先）')).toBeInTheDocument()
    const crawledSort = screen.getByRole('button', { name: /最近抓取时间.*点击切换为降序/ })
    await fireEvent.click(crawledSort)
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('sort_by=last_crawled_at'))
    expect(await screen.findByRole('columnheader', { name: /最近抓取时间/ })).toHaveAttribute(
      'aria-sort',
      'descending',
    )

    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('page=2'))

    await waitFor(() => expect(screen.getByRole('button', { name: '清空筛选' })).toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '清空筛选' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toBe('/api/v1/policies?page=1&page_size=20&sort_by=published_at&sort_order=desc'))
  })

  it('筛选元数据失败时保留政策列表并显示独立错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/policies/filters')) {
          return Promise.resolve(
            jsonResponse(
              { error: { code: 'FILTERS_FAILED', message: '筛选选项加载失败。', request_id: 'id', details: {} } },
              503,
            ),
          )
        }
        return Promise.resolve(jsonResponse(policyPage()))
      }),
    )

    await renderList('/policies?publisher=历史部门&category_id=99&source_id=88')

    expect(await screen.findByText('中共中央政治局召开会议')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('筛选选项加载失败。')
    expect(screen.getByLabelText('发布部门')).toHaveValue('历史部门')
    expect(screen.getByRole('option', { name: '历史部门（当前筛选）' })).toBeInTheDocument()
    expect(screen.getByLabelText('政策类别')).toHaveValue('99')
    expect(screen.getByRole('option', { name: '当前类别 #99' })).toBeInTheDocument()
    expect(screen.getByLabelText('政策来源')).toHaveValue('88')
    expect(screen.getByRole('option', { name: '当前来源 #88' })).toBeInTheDocument()
  })

  it('浏览器后退时恢复 URL 查询表单并重新加载列表', async () => {
    vi.stubGlobal('fetch', successfulFetch())
    const { router } = await renderList()
    expect(await screen.findByText('中共中央政治局召开会议')).toBeInTheDocument()

    await fireEvent.update(screen.getByLabelText('标题关键词'), '第一次')
    await fireEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => expect(router.currentRoute.value.query.keyword).toBe('第一次'))

    await fireEvent.update(screen.getByLabelText('标题关键词'), '第二次')
    await fireEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => expect(router.currentRoute.value.query.keyword).toBe('第二次'))

    router.back()
    await waitFor(() => expect(router.currentRoute.value.query.keyword).toBe('第一次'))
    await waitFor(() => expect(screen.getByLabelText('标题关键词')).toHaveValue('第一次'))
  })

  it('忽略较慢的旧查询响应，只展示最新 URL 对应结果', async () => {
    const oldResponse = deferredResponse()
    const latestResponse = deferredResponse()
    let listRequests = 0
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string | URL | Request) => {
        if (String(input).includes('/policies/filters')) {
          return Promise.resolve(jsonResponse(filterOptions()))
        }
        listRequests += 1
        return listRequests === 1 ? oldResponse.promise : latestResponse.promise
      }),
    )
    const { router } = await renderList('/policies?keyword=旧查询')
    await waitFor(() => expect(listRequests).toBe(1))

    await router.push('/policies?keyword=最新查询')
    await waitFor(() => expect(listRequests).toBe(2))
    const baseItem = policyPage().items[0]
    latestResponse.resolve(
      jsonResponse(policyPage({ items: [{ ...baseItem, title: '最新查询结果' }], total: 1 })),
    )
    expect(await screen.findByText('最新查询结果')).toBeInTheDocument()

    oldResponse.resolve(jsonResponse(policyPage({ items: [{ ...baseItem, title: '旧查询结果' }], total: 1 })))
    await waitFor(() => expect(screen.queryByText('旧查询结果')).not.toBeInTheDocument())
    expect(screen.getByText('最新查询结果')).toBeInTheDocument()
  })

  it('展示空状态和 API 错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string | URL | Request) =>
        Promise.resolve(
          jsonResponse(String(input).includes('/policies/filters') ? filterOptions() : { ...policyPage(), items: [], total: 0 }),
        ),
      ),
    )
    await renderList()
    expect(await screen.findByText('暂无政策')).toBeInTheDocument()

    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string | URL | Request) =>
        Promise.resolve(
          String(input).includes('/policies/filters')
            ? jsonResponse(filterOptions())
            : jsonResponse(
                {
                  error: {
                    code: 'POLICY_QUERY_INVALID',
                    message: '政策查询参数无效。',
                    request_id: 'request-id',
                    details: {},
                  },
                },
                422,
              ),
        ),
      ),
    )
    await renderList()
    expect(await screen.findByRole('alert')).toHaveTextContent('政策查询参数无效。')
  })

  it('勾选政策并创建分词分析任务', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input)
      if (url.includes('/policies/filters')) return Promise.resolve(jsonResponse(filterOptions()))
      if (url.includes('/policies')) return Promise.resolve(jsonResponse(policyPage()))
      if (url.includes('/analysis/tasks'))
        return Promise.resolve(jsonResponse({ task_id: 51, status: 'pending' }))
      return Promise.resolve(jsonResponse({}))
    })
    vi.stubGlobal('fetch', fetchMock)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/policies', name: 'policies', component: PolicyListView },
        { path: '/policies/:policyId', name: 'policy-detail', component: PolicyDetailView },
        { path: '/analysis', name: 'analysis', component: { template: '<div>analysis</div>' } },
      ],
    })
    await router.push('/policies')
    render(PolicyListView, { global: { plugins: [router] } })

    expect(await screen.findByText('中共中央政治局召开会议')).toBeInTheDocument()
    await fireEvent.click(screen.getByLabelText('选择 中共中央政治局召开会议'))
    await fireEvent.click(screen.getByRole('button', { name: /分词分析/ }))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            String(call[0]).includes('/analysis/tasks') &&
            (call[1] as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true),
    )
    await waitFor(() => expect(router.currentRoute.value.name).toBe('analysis'))
  })

  it('选择两篇政策并创建政策比对任务', async () => {
    const first = policyPage().items[0]
    const page = policyPage({
      items: [first, { ...first, id: 8, title: '人工智能安全治理规划', canonical_url: 'https://news.cn/example/d.html' }],
      total: 2,
    })
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input)
      if (url.includes('/policies/filters')) return Promise.resolve(jsonResponse(filterOptions()))
      if (url.includes('/policies')) return Promise.resolve(jsonResponse(page))
      if (url.includes('/analysis/comparison-tasks')) return Promise.resolve(jsonResponse({ task_id: 52, status: 'pending' }))
      return Promise.resolve(jsonResponse({}))
    })
    vi.stubGlobal('fetch', fetchMock)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/policies', name: 'policies', component: PolicyListView },
        { path: '/policies/:policyId', name: 'policy-detail', component: PolicyDetailView },
        { path: '/analysis', name: 'analysis', component: { template: '<div>analysis</div>' } },
      ],
    })
    await router.push('/policies')
    render(PolicyListView, { global: { plugins: [router] } })

    await screen.findByText('人工智能安全治理规划')
    expect(screen.getByRole('button', { name: '政策比对' })).toBeDisabled()
    await fireEvent.click(screen.getByLabelText('选择 中共中央政治局召开会议'))
    await fireEvent.click(screen.getByLabelText('选择 人工智能安全治理规划'))
    await fireEvent.click(screen.getByRole('button', { name: /政策比对/ }))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('/analysis/comparison-tasks'))).toBe(true),
    )
    await waitFor(() => expect(router.currentRoute.value.query.taskId).toBe('52'))
  })
})
