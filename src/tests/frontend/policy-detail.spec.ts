import { render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PolicyDetailView from '../../app/frontend/src/views/policies/PolicyDetailView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('政策详情', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('以纯文本显示正文和来源元数据', async () => {
    const content = '<script>window.hacked=true</script>政策正文\n\n第二段包含很长的无空格文本ABCDEFGHIJKLMN'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          id: 7,
          title: '中共中央政治局召开会议',
          content_text: content,
          canonical_url: 'https://news.cn/example/c.html',
          publisher: '新华社',
          category: { id: 1, code: 'politburo_meeting', name: '中央政治局会议' },
          source: { id: 1, code: 'xinhua', name: '新华网' },
          published_at: '2026-07-30T06:00:00Z',
          first_crawled_at: '2026-07-31T04:00:00Z',
          last_crawled_at: '2026-07-31T04:00:00Z',
          content_hash: 'abc123',
          latest_task_id: 9,
        }),
      ),
    )

    render(PolicyDetailView, { props: { policyId: 7 } })

    expect(await screen.findByRole('heading', { name: '中共中央政治局召开会议' })).toBeInTheDocument()
    const contentRegion = screen.getByRole('region', { name: '政策正文' })
    expect(contentRegion.querySelector('.policy-content')?.textContent).toBe(content)
    expect(contentRegion.querySelector('pre')).toBeNull()
    expect(screen.getByText('新华社')).toBeInTheDocument()
    expect(screen.getByText('新华网 · 中央政治局会议')).toBeInTheDocument()
    expect(screen.getByText('2026-07-30 14:00:00')).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('.policy-detail-header')).not.toBeNull()
    expect(document.querySelector('.policy-actions')).not.toBeNull()
    expect(screen.getByRole('link', { name: '打开原文' })).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('详情失败时展示后端安全错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: 'POLICY_NOT_FOUND',
              message: '政策不存在。',
              request_id: 'request-id',
              details: {},
            },
          },
          404,
        ),
      ),
    )

    render(PolicyDetailView, { props: { policyId: 404 } })

    expect(await screen.findByRole('alert')).toHaveTextContent('政策不存在。')
  })
})
