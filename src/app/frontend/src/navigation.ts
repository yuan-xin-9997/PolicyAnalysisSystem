import type { PageCode } from './stores/auth'

export interface NavigationItem {
  code: PageCode
  label: string
  path: string
}

export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { code: 'policies', label: '政策数据库', path: '/policies' },
  { code: 'tasks', label: '任务中心', path: '/tasks' },
  { code: 'push', label: '推送管理', path: '/push' },
  { code: 'analysis', label: '政策分析', path: '/analysis' },
  { code: 'users', label: '权限管理', path: '/users' },
  { code: 'settings', label: '系统配置', path: '/settings' },
] as const
