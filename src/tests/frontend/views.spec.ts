import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PlaceholderView from '../../app/frontend/src/views/PlaceholderView.vue'
import SettingsView from '../../app/frontend/src/views/SettingsView.vue'
import UsersView from '../../app/frontend/src/views/UsersView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('基础管理页面', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('用户页按批准的分页排序参数读取并展示安全字段', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [
          { id: 1, username: 'admin', role: 'admin', is_active: true, pages: [] },
          { id: 2, username: 'reader', role: 'user', is_active: false, pages: ['policies'] },
        ],
        total: 2,
        page: 1,
        page_size: 20,
        sort_by: 'username',
        sort_order: 'asc',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(UsersView, { global: { plugins: [createPinia()] } })

    expect(await screen.findByText('reader')).toBeInTheDocument()
    expect(screen.getByText('停用')).toBeInTheDocument()
    expect(screen.queryByText(/password/i)).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/users?page=1&page_size=20&sort_by=username&sort_order=asc',
      expect.any(Object),
    )
  })

  it('用户页提供加载、错误与空状态', async () => {
    const pending = new Promise<Response>(() => undefined)
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending))
    const rendered = render(UsersView, { global: { plugins: [createPinia()] } })
    expect(screen.getByRole('status')).toHaveTextContent('正在加载用户')
    rendered.unmount()

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0, page: 1, page_size: 20 })),
    )
    render(UsersView, { global: { plugins: [createPinia()] } })
    expect(await screen.findByText('暂无用户')).toBeInTheDocument()
  })

  it('配置页安全展示生效值、来源和 WebFetch 配置状态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          values: {
            server: { port: 30080 },
            webfetch: { api_key: '********', base_url: 'http://fetch.internal' },
          },
          sources: {
            'server.port': 'config_file',
            'webfetch.api_key': 'environment',
            'webfetch.base_url': 'config_file',
          },
          webfetch: { status: 'configured', checked: false },
        }),
      ),
    )

    render(SettingsView, { global: { plugins: [createPinia()] } })

    expect(await screen.findByText(/已配置/)).toBeInTheDocument()
    expect(screen.getByText('server.port')).toBeInTheDocument()
    expect(screen.getAllByText('配置文件')).toHaveLength(2)
    expect(screen.getByText('********')).toBeInTheDocument()
  })

  it('推送与分析占位页严格只读，不提供不可执行按钮', async () => {
    const { rerender } = render(PlaceholderView, { props: { page: 'push' } })
    expect(screen.getByRole('heading', { name: '推送管理' })).toBeInTheDocument()
    expect(screen.getByText('功能规划中')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    await rerender({ page: 'analysis' })
    expect(screen.getByRole('heading', { name: '政策分析' })).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('请求失败时展示后端安全错误消息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: 'SETTINGS_UNAVAILABLE',
              message: '配置暂时不可用。',
              request_id: 'request-id',
              details: {},
            },
          },
          503,
        ),
      ),
    )

    render(SettingsView, { global: { plugins: [createPinia()] } })

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('配置暂时不可用。'))
  })
})
