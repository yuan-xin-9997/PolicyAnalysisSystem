import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AiAccessView from '../../app/frontend/src/views/AiAccessView.vue'
import { useAuthStore } from '../../app/frontend/src/stores/auth'

const info = {
  api_url: 'https://example.org/api/v1', openapi_url: 'https://example.org/openapi.json',
  docs_url: 'https://example.org/docs', mcp_url: 'https://example.org/mcp/',
  token_url: 'https://example.org/oauth/token', issuer: 'https://example.org',
  scopes: [{ code: 'policies:read', label: '查看政策', description: '读取政策', read_only: true },
    { code: 'analysis:write', label: '执行政策分析', description: '创建任务', read_only: false }],
  tools: [{ name: 'search_policies', description: '查询政策', annotations: { readOnlyHint: true } }],
  clients: [{ name: '官方 Python MCP SDK', status: '已实测', description: '自动换令牌' }],
}
function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
async function open(): Promise<void> {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/ai-access', component: AiAccessView }] })
  await router.push('/settings/ai-access')
  const pinia = createPinia()
  useAuthStore(pinia).user = { id: 1, username: 'tester', role: 'admin', pages: ['policies'] }
  render(RouterView, { global: { plugins: [pinia, router] } })
  await screen.findByRole('button', { name: '创建凭据' })
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新权限与列表' })).toBeEnabled())
}

describe('AI 接入凭据管理', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each(['logout', 'pagehide'])('创建请求中发生 %s 后，迟到的密钥不能再次显示', async (event) => {
    let finish: (response: Response) => void = () => undefined
    vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return new Promise<Response>(resolve => { finish = resolve })
      if (path === '/api/v1/ai-access') return response(info)
      return response({ items: [], total: 0, page: 1, page_size: 10 })
    }))
    await open()
    await fireEvent.update(screen.getByLabelText('凭据名称'), '迟到请求')
    await fireEvent.click(screen.getByRole('button', { name: '创建凭据' }))
    await screen.findByRole('button', { name: '正在创建…' })
    if (event === 'logout') useAuthStore().clear()
    else window.dispatchEvent(new Event('pagehide'))
    finish(response({ id: 'late-id', name: 'late', secret: 'fixture-late-secret' }, 201))
    await waitFor(() => expect(screen.queryByRole('button', { name: '正在创建…' })).not.toBeInTheDocument())
    expect(screen.queryByLabelText('完整密钥，仅显示一次')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('fixture-late-secret')
  })

  it('默认只读、密钥仅显示一次，复制失败仍保留，可确认关闭并撤销', async () => {
    const secret = 'fixture-only-opaque-key'
    let created = false
    const entry = { id: 'credential-id', name: '测试助手', scopes: ['policies:read'], effective_scopes: ['policies:read'],
      created_at: '2026-10-25T00:00:00Z', expires_at: '2026-11-01T00:00:00Z', status: 'active', revoked_at: null }
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') { created = true; return response({ ...entry, secret }, 201) }
      if (options?.method === 'DELETE') { entry.status = 'revoked'; return new Response(null, { status: 204 }) }
      if (path === '/api/v1/ai-access') return response(info)
      return response({ items: created ? [entry] : [], total: created ? 1 : 0, page: 1, page_size: 10 })
    })
    vi.stubGlobal('fetch', fetch)
    Object.defineProperty(navigator, 'clipboard', { configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('clipboard unavailable')) } })
    await open()
    expect(screen.getByLabelText('查看政策')).toBeChecked()
    expect(screen.getByLabelText('执行政策分析')).not.toBeChecked()
    await fireEvent.update(screen.getByLabelText('凭据名称'), '测试助手')
    await fireEvent.click(screen.getByRole('button', { name: '创建凭据' }))
    expect(await screen.findByLabelText('完整密钥，仅显示一次')).toHaveValue(secret)
    await fireEvent.click(screen.getByRole('button', { name: '复制密钥' }))
    expect(await screen.findByText('复制失败，请手动选中并保存密钥。')).toBeInTheDocument()
    expect(screen.getByLabelText('完整密钥，仅显示一次')).toHaveValue(secret)
    expect(localStorage.getItem('api_key')).toBeNull()
    expect(sessionStorage.getItem('api_key')).toBeNull()
    expect(location.href).not.toContain(secret)
    await fireEvent.click(screen.getByLabelText('我已安全保存密钥'))
    await fireEvent.click(screen.getByRole('button', { name: '关闭并清除密钥' }))
    await waitFor(() => expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument())
    await fireEvent.click(await screen.findByRole('button', { name: '撤销' }))
    expect(screen.getByText(/关联的短期令牌也会立即失效/)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '确认撤销' }))
    expect(await screen.findByText('已撤销')).toBeInTheDocument()
  })

  it('加载失败可重试，无权限时不能创建', async () => {
    let fail = true
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      if (fail) return response({ error: { message: '暂时不可用' } }, 503)
      if (path === '/api/v1/ai-access') return response({ ...info, scopes: [] })
      return response({ items: [], total: 0, page: 1, page_size: 10 })
    }))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/ai-access', component: AiAccessView }] })
    await router.push('/settings/ai-access')
    const pinia = createPinia()
    useAuthStore(pinia).user = { id: 1, username: 'tester', role: 'admin', pages: ['policies'] }
    render(RouterView, { global: { plugins: [pinia, router] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时不可用')
    fail = false
    await fireEvent.click(screen.getByRole('button', { name: '重试加载' }))
    expect(await screen.findByRole('button', { name: '创建凭据' })).toBeDisabled()
    expect(screen.getByText(/当前没有可授予的业务权限/)).toBeInTheDocument()
  })
})
