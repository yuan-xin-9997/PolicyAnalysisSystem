import { render, screen } from '@testing-library/vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PolicyDetailView from '../../app/frontend/src/views/policies/PolicyDetailView.vue'

function loadMainCss(): string {
  const candidates = [
    resolve(process.cwd(), 'src/styles/main.css'),
    resolve(process.cwd(), 'src/app/frontend/src/styles/main.css'),
  ]
  for (const candidate of candidates) {
    try {
      return readFileSync(candidate, 'utf-8')
    } catch {
      continue
    }
  }
  throw new Error(`main.css not found in: ${candidates.join(', ')}`)
}

const mainCss = loadMainCss()

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const detailOverrides = {
  id: 7,
  title: '中共中央政治局召开会议',
  canonical_url: 'https://news.cn/example/c.html',
  publisher: '新华社',
  category: { id: 1, code: 'politburo_meeting', name: '中央政治局会议' },
  source: { id: 1, code: 'xinhua', name: '新华网' },
  published_at: '2026-07-30T06:00:00Z',
  first_crawled_at: '2026-07-31T04:00:00Z',
  last_crawled_at: '2026-07-31T04:00:00Z',
  content_hash: 'abc123',
  latest_task_id: 9,
}

function stubPolicyDetail(content: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse({ ...detailOverrides, content_text: content })),
  )
}

describe('政策详情', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('将正文按换行与空行切分为独立段落', async () => {
    stubPolicyDetail('第一段政策正文。\n\n第二段政策正文。\n第三段政策正文。')
    render(PolicyDetailView, { props: { policyId: 7 } })

    await screen.findByRole('heading', { name: '中共中央政治局召开会议' })
    const paragraphs = screen
      .getByRole('region', { name: '政策正文' })
      .querySelectorAll('.policy-content p')
    expect(paragraphs).toHaveLength(3)
    expect(paragraphs[0].textContent).toBe('第一段政策正文。')
    expect(paragraphs[1].textContent).toBe('第二段政策正文。')
    expect(paragraphs[2].textContent).toBe('第三段政策正文。')
  })

  it('正文段落以 <p> 渲染并声明首行缩进样式', async () => {
    stubPolicyDetail('政策正文段落。')
    render(PolicyDetailView, { props: { policyId: 7 } })

    await screen.findByRole('heading', { name: '中共中央政治局召开会议' })
    const paragraph = screen
      .getByRole('region', { name: '政策正文' })
      .querySelector('.policy-content p')
    expect(paragraph).not.toBeNull()
    expect(mainCss).toMatch(/\.policy-content\s+p\s*\{[^}]*text-indent:\s*2em/)
  })

  it('以分段纯文本显示正文并保留来源元数据，不执行 HTML', async () => {
    const content =
      '<script>window.hacked=true</script>政策正文\n\n第二段包含很长的无空格文本ABCDEFGHIJKLMN'
    stubPolicyDetail(content)
    render(PolicyDetailView, { props: { policyId: 7 } })

    await screen.findByRole('heading', { name: '中共中央政治局召开会议' })
    const contentRegion = screen.getByRole('region', { name: '政策正文' })
    const paragraphs = contentRegion.querySelectorAll('.policy-content p')
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[0].textContent).toBe('<script>window.hacked=true</script>政策正文')
    expect(paragraphs[1].textContent).toBe('第二段包含很长的无空格文本ABCDEFGHIJKLMN')
    expect(contentRegion.querySelector('pre')).toBeNull()
    expect(document.querySelector('script')).toBeNull()
    expect(screen.getByText('新华社')).toBeInTheDocument()
    expect(screen.getByText('新华网 · 中央政治局会议')).toBeInTheDocument()
    expect(screen.getByText('2026-07-30 14:00:00')).toBeInTheDocument()
    expect(document.querySelector('.policy-detail-header')).not.toBeNull()
    expect(document.querySelector('.policy-actions')).not.toBeNull()
    expect(screen.getByRole('link', { name: '打开原文' })).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('无原始换行的存量正文作为单段渲染并施加首行缩进，不执行 HTML', async () => {
    // 模拟 WebFetch 扁平化正文或历史存量：单行无换行，且夹带 HTML 注入尝试。
    const flat =
      '新华社北京7月30日电 中共中央政治局召开会议，分析研究当前经济形势。会议还研究了其他事项。<img src=x onerror=alert(1)>'
    stubPolicyDetail(flat)
    render(PolicyDetailView, { props: { policyId: 7 } })

    await screen.findByRole('heading', { name: '中共中央政治局召开会议' })
    const paragraphs = screen
      .getByRole('region', { name: '政策正文' })
      .querySelectorAll('.policy-content p')
    // 无换行 -> 整体作为单段渲染，不报错、不拆分。
    expect(paragraphs).toHaveLength(1)
    expect(paragraphs[0].textContent).toBe(flat)
    // 单段仍声明首行缩进。
    expect(mainCss).toMatch(/\.policy-content\s+p\s*\{[^}]*text-indent:\s*2em/)
    // 文本插值不执行 HTML：img 未被渲染为元素。
    expect(document.querySelector('img')).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
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
