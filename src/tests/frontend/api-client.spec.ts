import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  apiRequest,
  setCsrfTokenProvider,
  setUnauthorizedHandler,
} from '../../app/frontend/src/api/client'

describe('API 客户端', () => {
  afterEach(() => {
    setCsrfTokenProvider(() => '')
    setUnauthorizedHandler(() => undefined)
    vi.unstubAllGlobals()
  })

  it('只请求同源 API、携带会话，并为状态变更附加 CSRF', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfTokenProvider(() => 'csrf-safe-token')

    await apiRequest<void>('/users/reader/status', {
      method: 'PATCH',
      body: JSON.stringify({ is_active: false }),
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/users/reader/status')
    const options = fetchMock.mock.calls[0][1] as RequestInit
    expect(options.credentials).toBe('include')
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf-safe-token')
  })

  it('登录不依赖预存 CSRF 且正确处理空响应', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfTokenProvider(() => 'stale-token')

    await expect(apiRequest('/auth/login', { method: 'POST' })).resolves.toBeUndefined()
    const options = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(options.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('保留安全错误字段并在 401 时调用可注入处理器', async () => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'INVALID_SESSION',
              message: '登录状态已失效。',
              request_id: 'request-safe-id',
              details: { reason: 'expired' },
            },
          }),
          { status: 401, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const error = await apiRequest('/auth/me').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 401,
      code: 'INVALID_SESSION',
      message: '登录状态已失效。',
      requestId: 'request-safe-id',
      details: { reason: 'expired' },
    })
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('401 不等待导航处理器完成，避免与路由守卫互相阻塞', async () => {
    setUnauthorizedHandler(() => new Promise<void>(() => undefined))
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'INVALID_SESSION',
              message: '登录状态已失效。',
              request_id: 'request-safe-id',
              details: {},
            },
          }),
          { status: 401, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const result = await Promise.race([
      apiRequest('/auth/me').catch((error: unknown) => error),
      new Promise((resolve) => setTimeout(() => resolve('handler-timeout'), 50)),
    ])

    expect(result).toBeInstanceOf(ApiError)
  })
})
