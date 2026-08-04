import type { Pinia } from 'pinia'
import {
  createRouter,
  createWebHistory,
  type Router,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore, type PageCode } from '../stores/auth'
import LoginView from '../views/LoginView.vue'
import NoAccessView from '../views/NoAccessView.vue'
import PlaceholderView from '../views/PlaceholderView.vue'
import PolicyDetailView from '../views/policies/PolicyDetailView.vue'
import PolicyListView from '../views/policies/PolicyListView.vue'
import SettingsView from '../views/SettingsView.vue'
import TaskDetailView from '../views/tasks/TaskDetailView.vue'
import TaskListView from '../views/tasks/TaskListView.vue'
import UsersView from '../views/UsersView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    page?: PageCode
  }
}

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: LoginView },
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: 'policies', name: 'policies', component: PolicyListView, meta: { page: 'policies' } },
      {
        path: 'policies/:policyId',
        name: 'policy-detail',
        component: PolicyDetailView,
        props: true,
        meta: { page: 'policies' },
      },
      { path: 'tasks', name: 'tasks', component: TaskListView, meta: { page: 'tasks' } },
      {
        path: 'tasks/:taskId',
        name: 'task-detail',
        component: TaskDetailView,
        props: true,
        meta: { page: 'tasks' },
      },
      { path: 'push', name: 'push', component: PlaceholderView, props: { page: 'push' }, meta: { page: 'push' } },
      { path: 'analysis', name: 'analysis', component: PlaceholderView, props: { page: 'analysis' }, meta: { page: 'analysis' } },
      { path: 'users', name: 'users', component: UsersView, meta: { page: 'users' } },
      { path: 'settings', name: 'settings', component: SettingsView, meta: { page: 'settings' } },
      { path: 'no-access', name: 'no-access', component: NoAccessView },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export function createPolicyRouter(
  pinia: Pinia,
  history: RouterHistory = createWebHistory(),
): Router {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuthStore(pinia)
    const isLogin = to.name === 'login'

    const restored = await auth.initialize()
    if (!restored) {
      return isLogin ? true : { name: 'login' }
    }
    if (isLogin && auth.user) return auth.firstAccessiblePath()
    if (to.path === '/') return auth.firstAccessiblePath()
    if (to.meta.page && !auth.canAccess(to.meta.page)) return auth.firstAccessiblePath()
    if (to.name === 'no-access' && auth.firstAccessiblePath() !== '/no-access') {
      return auth.firstAccessiblePath()
    }
    return true
  })

  return router
}
