import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AnalysisView from '../../app/frontend/src/views/analysis/AnalysisView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const succeededTask = {
  id: 51,
  task_type: 'word_frequency',
  status: 'succeeded',
  policy_count: 2,
  requested_by: 1,
  started_at: '2026-07-31T04:00:00Z',
  finished_at: '2026-07-31T04:01:00Z',
  error_summary: null,
  created_at: '2026-07-31T04:00:00Z',
}

const words = {
  items: [
    { word: '人工智能', frequency: 12, tfidf: 0.92, doc_count: 2 },
    { word: '产业', frequency: 9, tfidf: 0.45, doc_count: 2 },
  ],
  total: 2,
}

const relations = {
  items: [{ word1: '产业', word2: '人工智能', co_count: 2 }],
  nodes: ['人工智能', '产业'],
}

const logs = {
  items: [{ id: 1, level: 'info', message: '词频分析完成。', context: {}, created_at: '2026-07-31T04:01:00Z' }],
  total: 1,
  page: 1,
  page_size: 50,
}

function mockFetch(task: object = succeededTask, list: object[] = [succeededTask]) {
  return vi.fn().mockImplementation((input: string | URL | Request) => {
    const url = String(input)
    if (url.includes('/words')) return Promise.resolve(jsonResponse(words))
    if (url.includes('/relations')) return Promise.resolve(jsonResponse(relations))
    if (url.includes('/logs')) return Promise.resolve(jsonResponse(logs))
    if (url.includes('/analysis/tasks/')) return Promise.resolve(jsonResponse(task))
    if (url.includes('/analysis/tasks'))
      return Promise.resolve(jsonResponse({ items: list, total: list.length, page: 1, page_size: 20 }))
    return Promise.resolve(jsonResponse({}))
  })
}

async function renderAnalysis(path = '/analysis?taskId=51') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/analysis', name: 'analysis', component: AnalysisView }],
  })
  await router.push(path)
  return render(AnalysisView, { global: { plugins: [router] } })
}

describe('政策分析', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('展示已完成任务的词频排行与历史任务', async () => {
    vi.stubGlobal('fetch', mockFetch())
    await renderAnalysis()
    expect(await screen.findByText('任务 #51')).toBeInTheDocument()
    expect(await screen.findByText('人工智能')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('0.920')).toBeInTheDocument()
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '#51' })).toBeInTheDocument()
  })

  it('切换词云与关系图 Tab', async () => {
    vi.stubGlobal('fetch', mockFetch())
    await renderAnalysis()
    await screen.findByText('人工智能')
    await fireEvent.click(screen.getByRole('tab', { name: '词云' }))
    expect(screen.getByRole('tab', { name: '词云' })).toHaveAttribute('aria-selected', 'true')
    await fireEvent.click(screen.getByRole('tab', { name: '关键词关系图' }))
    expect(screen.getByRole('tab', { name: '关键词关系图' })).toHaveAttribute('aria-selected', 'true')
  })

  it('按 TF-IDF 排序重新请求词频', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    await renderAnalysis()
    await screen.findByText('人工智能')
    await fireEvent.click(screen.getByRole('button', { name: '按 TF-IDF 排序' }))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('sort_by=tfidf'))).toBe(true),
    )
  })

  it('无任务时展示历史空状态', async () => {
    vi.stubGlobal('fetch', mockFetch(succeededTask, []))
    await renderAnalysis('/analysis')
    expect(await screen.findByText('暂无历史任务，前往政策数据库选择政策开始分析。')).toBeInTheDocument()
  })

  it('失败任务展示错误摘要', async () => {
    const failedTask = { ...succeededTask, status: 'failed', finished_at: '2026-07-31T04:01:00Z', error_summary: '分析执行异常。' }
    vi.stubGlobal('fetch', mockFetch(failedTask, [failedTask]))
    await renderAnalysis()
    expect(await screen.findByText('任务 #51')).toBeInTheDocument()
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0)
    expect(screen.getByText('分析执行异常。')).toBeInTheDocument()
  })
})
