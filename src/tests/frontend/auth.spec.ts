import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { setCsrfTokenProvider } from '../../app/frontend/src/api/client'
import { createPolicyRouter } from '../../app/frontend/src/router'
import { useAuthStore } from '../../app/frontend/src/stores/auth'
import LoginView from '../../app/frontend/src/views/LoginView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('认证状态', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('登录成功映射 page_permissions、保存 CSRF 并跳首个授权页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createPolicyRouter(pinia, createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          user: {
            id: 8,
            username: 'reader',
            role: 'user',
            page_permissions: ['tasks'],
          },
          csrf_token: 'csrf-test-token',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ version: 'v0.43', commit_sha: 'def5678' }))
    vi.stubGlobal('fetch', fetchMock)

    render(LoginView, {
      global: { plugins: [pinia, router] },
    })
    await fireEvent.update(screen.getByLabelText('用户名'), 'reader')
    await fireEvent.update(screen.getByLabelText('密码'), 'reader123')
    await fireEvent.click(screen.getByRole('button', { name: '登录' }))

    const auth = useAuthStore()
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/tasks'))
    expect(auth.user).toEqual({ id: 8, username: 'reader', role: 'user', pages: ['tasks'] })
    expect(auth.csrfToken).toBe('csrf-test-token')
    expect(auth.version).toBe('v0.43')
    expect(sessionStorage.getItem('policy-analysis.csrf-token')).toBe('csrf-test-token')
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/auth/login',
      '/api/v1/system/info',
    ])
  })

  it('统一展示后端安全登录错误、清空密码并恢复按钮', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createPolicyRouter(pinia, createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: 'INVALID_CREDENTIALS',
              message: '用户名或密码错误。',
              request_id: 'request-id',
              details: {},
            },
          },
          401,
        ),
      ),
    )

    render(LoginView, {
      global: { plugins: [pinia, router] },
    })
    await fireEvent.update(screen.getByLabelText('用户名'), 'missing')
    const password = screen.getByLabelText('密码') as HTMLInputElement
    await fireEvent.update(password, 'does-not-leak')
    await fireEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码错误。')
    expect(password.value).toBe('')
    expect(screen.getByRole('button', { name: '登录' })).not.toBeDisabled()
  })

  it('退出携带 CSRF，并且无论响应如何都清理身份和 tab token', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'admin', role: 'admin', pages: [] }
    auth.csrfToken = 'logout-token'
    sessionStorage.setItem('policy-analysis.csrf-token', 'logout-token')
    setCsrfTokenProvider(() => auth.csrfToken)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await auth.logout()

    const options = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('logout-token')
    expect(auth.user).toBeNull()
    expect(auth.csrfToken).toBe('')
    expect(sessionStorage.getItem('policy-analysis.csrf-token')).toBeNull()
  })

  it('恢复会话时读取 page_permissions 并填充系统版本', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse({ id: 4, username: 'reader', role: 'user', page_permissions: ['policies'] }),
        )
        .mockResolvedValueOnce(jsonResponse({ version: 'v0.42', commit_sha: 'abc1234' })),
    )
    const auth = useAuthStore()

    await auth.restore()

    expect(auth.user?.pages).toEqual(['policies'])
    expect(auth.version).toBe('v0.42')
  })
})
