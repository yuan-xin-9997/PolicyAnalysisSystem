import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TaskDetailView from '../../app/frontend/src/views/tasks/TaskDetailView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const runningTask = {
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
}

describe('任务详情', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('展示统计、进度、明细 reason、日志并支持取消运行任务', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => {
      if (path.endsWith('/items')) {
        return Promise.resolve(
          jsonResponse({
            items: [{ id: 1, candidate_url: 'https://news.cn/a', normalized_url: null, status: 'failed', policy_id: null, attempt_count: 1, reason_code: 'CONTENT_TOO_SHORT', reason_message: '正文过短', started_at: null, finished_at: null }],
            total: 1,
            page: 1,
            page_size: 50,
          }),
        )
      }
      if (path.endsWith('/logs')) {
        return Promise.resolve(
          jsonResponse({
            items: [{ id: 1, level: 'warning', message: '<b>已脱敏</b>', context: { html: '<img>' }, created_at: '2026-07-31T04:00:00Z' }],
            total: 1,
            page: 1,
            page_size: 50,
          }),
        )
      }
      if (path.endsWith('/cancel')) return Promise.resolve(jsonResponse({ ...runningTask, status: 'cancelled', cancel_requested_at: '2026-07-31T04:01:00Z' }))
      return Promise.resolve(jsonResponse(runningTask))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(TaskDetailView, { props: { taskId: 9 } })

    expect((await screen.findAllByText('运行')).length).toBeGreaterThan(0)
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
    expect(screen.getByText('CONTENT_TOO_SHORT 正文过短')).toBeInTheDocument()
    expect(screen.getByText('<b>已脱敏</b>')).toBeInTheDocument()
    expect(document.querySelector('b')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '取消任务' }))
    await waitFor(() => expect(screen.getByText('已取消')).toBeInTheDocument())
  })

  it('终态隐藏取消按钮并展示五个统计数', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path: string) => {
        if (path.endsWith('/items') || path.endsWith('/logs')) {
          return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 50 }))
        }
        return Promise.resolve(jsonResponse({ ...runningTask, status: 'succeeded' }))
      }),
    )

    render(TaskDetailView, { props: { taskId: 9 } })

    expect((await screen.findAllByText('成功')).length).toBeGreaterThan(0)
    for (const value of ['1', '1', '0', '0', '2']) {
      expect(screen.getAllByText(value).length).toBeGreaterThan(0)
    }
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
  })
})
