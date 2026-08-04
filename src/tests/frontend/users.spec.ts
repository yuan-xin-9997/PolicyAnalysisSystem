import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import UsersView from '../../app/frontend/src/views/UsersView.vue'

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const usersResponse = {
  items: [
    { id: 1, username: 'admin', role: 'admin', is_active: true, pages: [] },
    { id: 2, username: 'reader', role: 'user', is_active: true, pages: ['policies'] },
  ],
  total: 2,
  page: 1,
  page_size: 20,
  sort_by: 'username',
  sort_order: 'asc',
}

describe('用户权限管理', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('新增、改角色、启停和页面授权都调用对应安全接口，页面不显示现有密码', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        return Promise.resolve(jsonResponse({ id: 3, username: 'operator', role: 'user', is_active: true, pages: ['tasks'] }, 201))
      }
      if (path.endsWith('/role')) return Promise.resolve(jsonResponse({ ...usersResponse.items[1], role: 'admin' }))
      if (path.endsWith('/status')) return Promise.resolve(jsonResponse({ ...usersResponse.items[1], is_active: false }))
      if (path.endsWith('/pages')) return Promise.resolve(jsonResponse({ ...usersResponse.items[1], pages: ['policies', 'tasks'] }))
      return Promise.resolve(jsonResponse(usersResponse))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(UsersView, { global: { plugins: [createPinia()] } })
    await screen.findByText('reader')
    expect(document.body.textContent || '').not.toMatch(/existing-password/i)

    const createForm = screen.getByRole('form', { name: '新增用户' })
    await fireEvent.update(screen.getByLabelText('用户名'), 'operator')
    await fireEvent.update(screen.getByLabelText('初始密码'), 'operator123')
    await fireEvent.update(screen.getByLabelText('角色'), 'user')
    await fireEvent.click(within(createForm).getByLabelText('任务中心'))
    await fireEvent.click(screen.getByRole('button', { name: '创建用户' }))
    await waitFor(() => expect(screen.getByText('operator')).toBeInTheDocument())

    const readerRow = screen.getByText('reader').closest('tr') as HTMLElement
    await fireEvent.click(within(readerRow).getByLabelText('任务中心'))
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/users/reader/pages'))).toBe(true))

    await fireEvent.click(within(readerRow).getByRole('button', { name: '停用' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/users/reader/status'))).toBe(true))

    await fireEvent.update(screen.getByLabelText('reader 角色'), 'admin')
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/users/reader/role'))).toBe(true))

    const body = JSON.parse(fetchMock.mock.calls.find(([path, options]) => path.endsWith('/users') && options?.method === 'POST')?.[1]?.body as string)
    expect(body).toMatchObject({ username: 'operator', password: 'operator123', role: 'user', pages: ['tasks'] })
  })

  it('重置密码只提交新密码，两次输入不一致时不请求接口，成功后清空表单', async () => {
    const fetchMock = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/password') && options?.method === 'PATCH') return Promise.resolve(jsonResponse(usersResponse.items[1]))
      return Promise.resolve(jsonResponse(usersResponse))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(UsersView, { global: { plugins: [createPinia()] } })
    await screen.findByText('reader')
    const password = screen.getByLabelText('reader 新密码') as HTMLInputElement
    const confirmation = screen.getByLabelText('reader 确认新密码') as HTMLInputElement
    await fireEvent.update(password, 'new-password-1')
    await fireEvent.update(confirmation, 'new-password-2')
    await fireEvent.submit(screen.getByRole('form', { name: 'reader 重置密码' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('两次输入的新密码不一致。')
    expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/password'))).toBe(false)

    await fireEvent.update(confirmation, 'new-password-1')
    await fireEvent.submit(screen.getByRole('form', { name: 'reader 重置密码' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/users/reader/password'))).toBe(true))
    await waitFor(() => expect(password.value).toBe(''))
    expect(confirmation.value).toBe('')
  })
})
