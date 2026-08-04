import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PolicyDetailView from '../../app/frontend/src/views/policies/PolicyDetailView.vue'
import PolicyListView from '../../app/frontend/src/views/policies/PolicyListView.vue'

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

async function renderList() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/policies', name: 'policies', component: PolicyListView },
      { path: '/policies/:policyId', name: 'policy-detail', component: PolicyDetailView },
    ],
  })
  await router.push('/policies?keyword=政治局')
  return render(PolicyListView, { global: { plugins: [router] } })
}

describe('政策列表', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('展示筛选、排序、分页和政策行', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(policyPage())))
    vi.stubGlobal('fetch', fetchMock)

    await renderList()

    expect(await screen.findByText('中共中央政治局召开会议')).toBeInTheDocument()
    expect(screen.getByText('新华社')).toBeInTheDocument()
    expect(screen.getByText('中央政治局会议')).toBeInTheDocument()
    expect(screen.getByText('2026-07-30 14:00:00')).toBeInTheDocument()
    expect(screen.getByText('2026-07-31 12:30:00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '中共中央政治局召开会议' })).toHaveAttribute(
      'href',
      '/policies/7',
    )

    await fireEvent.update(screen.getByLabelText('发布部门'), '新华社')
    await fireEvent.update(screen.getByLabelText('类别 ID'), '1')
    await fireEvent.update(screen.getByLabelText('来源 ID'), '1')
    await fireEvent.update(screen.getByLabelText('发布时间起'), '2026-07-01')
    await fireEvent.update(screen.getByLabelText('发布时间止'), '2026-07-31')
    await fireEvent.click(screen.getByRole('button', { name: '筛选' }))

    await waitFor(() =>
      expect(fetchMock.mock.calls.at(-1)?.[0]).toContain(
        'keyword=%E6%94%BF%E6%B2%BB%E5%B1%80&publisher=%E6%96%B0%E5%8D%8E%E7%A4%BE&category_id=1&source_id=1&published_from=2026-07-01T00%3A00%3A00%2B08%3A00&published_to=2026-07-31T23%3A59%3A59%2B08%3A00',
      ),
    )

    await waitFor(() => expect(screen.getByRole('button', { name: '最近抓取时间' })).toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '最近抓取时间' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('sort_by=last_crawled_at'))

    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('page=2'))

    await waitFor(() => expect(screen.getByRole('button', { name: '清空筛选' })).toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '清空筛选' }))
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toBe('/api/v1/policies?page=1&page_size=20&sort_by=published_at&sort_order=desc'))
  })

  it('展示空状态和 API 错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...policyPage(), items: [], total: 0 })))
    await renderList()
    expect(await screen.findByText('暂无政策')).toBeInTheDocument()

    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
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
    )
    await renderList()
    expect(await screen.findByRole('alert')).toHaveTextContent('政策查询参数无效。')
  })
})
