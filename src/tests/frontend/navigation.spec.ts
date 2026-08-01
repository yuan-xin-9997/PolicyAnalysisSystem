import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import App from '../../app/frontend/src/App.vue'
import { createPolicyRouter } from '../../app/frontend/src/router'
import { useAuthStore } from '../../app/frontend/src/stores/auth'

describe('权限导航', () => {
  it('普通用户只看到授权菜单，并在左下角显示身份和版本', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'reader', role: 'user', pages: ['policies'] }
    auth.version = 'v0.12'
    const router = createPolicyRouter(pinia, createMemoryHistory())
    await router.push('/policies')
    await router.isReady()

    render(App, { global: { plugins: [pinia, router] } })

    const navigation = screen.getByRole('navigation', { name: '主导航' })
    expect(navigation).toHaveTextContent('政策数据库')
    expect(within(navigation).queryByText('权限管理')).not.toBeInTheDocument()
    expect(screen.getByText('reader')).toBeInTheDocument()
    expect(screen.getByText('v0.12')).toBeInTheDocument()
  })

  it('管理员看到 PageCode 闭集中的全部菜单', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'admin', role: 'admin', pages: [] }
    const router = createPolicyRouter(pinia, createMemoryHistory())
    await router.push('/policies')
    await router.isReady()

    render(App, { global: { plugins: [pinia, router] } })

    const navigation = screen.getByRole('navigation', { name: '主导航' })
    for (const item of [
      '政策数据库',
      '任务中心',
      '推送管理',
      '政策分析',
      '权限管理',
      '系统配置',
    ]) {
      expect(within(navigation).getByText(item)).toBeInTheDocument()
    }
  })

  it('直接访问未授权路由时跳到首个授权页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'reader', role: 'user', pages: ['policies'] }
    const router = createPolicyRouter(pinia, createMemoryHistory())

    await router.push('/users')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/policies')
  })

  it('已登录访问登录页时跳到首个授权页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'reader', role: 'user', pages: ['tasks'] }
    const router = createPolicyRouter(pinia, createMemoryHistory())

    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/tasks')
  })

  it('没有页面权限的普通用户进入明确的无权限页且不循环', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'reader', role: 'user', pages: [] }
    const router = createPolicyRouter(pinia, createMemoryHistory())

    await router.push('/policies')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/no-access')
  })

  it('退出按钮调用退出并回到登录页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'admin', role: 'admin', pages: [] }
    vi.spyOn(auth, 'logout').mockImplementation(async () => auth.clear())
    const router = createPolicyRouter(pinia, createMemoryHistory())
    await router.push('/policies')
    await router.isReady()

    render(App, {
      global: {
        plugins: [pinia, router],
      },
    })
    await fireEvent.click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
  })
})
