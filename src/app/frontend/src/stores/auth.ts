import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiRequest } from '../api/client'
import { NAVIGATION_ITEMS } from '../navigation'

export type PageCode = 'policies' | 'tasks' | 'push' | 'analysis' | 'users' | 'settings'
export type UserRole = 'admin' | 'user'

export interface CurrentUser {
  id: number
  username: string
  role: UserRole
  pages: PageCode[]
}

interface ApiUser {
  id: number
  username: string
  role: UserRole
  page_permissions: PageCode[]
}

interface LoginResponse {
  user: ApiUser
  csrf_token: string
}

interface SystemInfo {
  version: string
}

const CSRF_STORAGE_KEY = 'policy-analysis.csrf-token'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const csrfToken = ref(readSessionToken())
  const version = ref('v0.dev')

  async function login(username: string, password: string): Promise<void> {
    const response = await apiRequest<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    user.value = fromApiUser(response.user)
    setCsrfToken(response.csrf_token)
    await loadVersion()
  }

  async function logout(): Promise<void> {
    try {
      await apiRequest<void>('/auth/logout', { method: 'POST' })
    } finally {
      clear()
    }
  }

  async function restore(): Promise<boolean> {
    try {
      const restoredUser = await apiRequest<ApiUser>('/auth/me')
      user.value = fromApiUser(restoredUser)
      await loadVersion()
      return true
    } catch {
      clear()
      return false
    }
  }

  function clear(): void {
    user.value = null
    setCsrfToken('')
  }

  function canAccess(page: PageCode): boolean {
    return user.value?.role === 'admin' || Boolean(user.value?.pages.includes(page))
  }

  function firstAccessiblePath(): string {
    if (!user.value) return '/login'
    return NAVIGATION_ITEMS.find((item) => canAccess(item.code))?.path || '/no-access'
  }

  function setCsrfToken(token: string): void {
    csrfToken.value = token
    try {
      if (token) sessionStorage.setItem(CSRF_STORAGE_KEY, token)
      else sessionStorage.removeItem(CSRF_STORAGE_KEY)
    } catch {
      // Session storage can be disabled; the in-memory token still protects this view.
    }
  }

  async function loadVersion(): Promise<void> {
    try {
      const systemInfo = await apiRequest<SystemInfo>('/system/info')
      version.value = systemInfo.version || 'v0.dev'
    } catch {
      version.value = 'v0.dev'
    }
  }

  return {
    user,
    csrfToken,
    version,
    login,
    logout,
    restore,
    clear,
    canAccess,
    firstAccessiblePath,
  }
})

function fromApiUser(user: ApiUser): CurrentUser {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    pages: user.page_permissions,
  }
}

function readSessionToken(): string {
  try {
    return sessionStorage.getItem(CSRF_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}
