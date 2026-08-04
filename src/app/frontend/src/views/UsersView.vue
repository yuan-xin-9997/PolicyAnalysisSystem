<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'
import type { PageCode, UserRole } from '../stores/auth'
import { NAVIGATION_ITEMS } from '../navigation'

interface ManagedUser {
  id: number
  username: string
  role: UserRole
  is_active: boolean
  pages: PageCode[]
}

interface UsersResponse {
  items: ManagedUser[]
  total: number
  page: number
  page_size: number
  sort_by: string
  sort_order: string
}

const users = ref<ManagedUser[]>([])
const total = ref(0)
const loading = ref(true)
const errorMessage = ref('')
const successMessage = ref('')
const createForm = reactive({ username: '', password: '', role: 'user' as UserRole, pages: [] as PageCode[] })
const passwordForms = reactive<Record<string, { password: string; confirmation: string }>>({})

const pageOptions = computed(() => NAVIGATION_ITEMS)

onMounted(loadUsers)

async function loadUsers(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await apiRequest<UsersResponse>(
      '/users?page=1&page_size=20&sort_by=username&sort_order=asc',
    )
    users.value = response.items
    total.value = response.total
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '用户列表加载失败。'
  } finally {
    loading.value = false
  }
}

function effectivePages(user: ManagedUser): PageCode[] {
  return user.role === 'admin' ? pageOptions.value.map((item) => item.code) : user.pages
}

function upsertUser(user: ManagedUser): void {
  const index = users.value.findIndex((item) => item.username === user.username)
  if (index >= 0) users.value[index] = user
  else {
    users.value = [user, ...users.value]
    total.value += 1
  }
}

async function createUser(): Promise<void> {
  await mutate(async () => {
    const user = await apiRequest<ManagedUser>('/users', {
      method: 'POST',
      body: JSON.stringify({
        username: createForm.username.trim(),
        password: createForm.password,
        role: createForm.role,
        pages: createForm.role === 'admin' ? [] : createForm.pages,
      }),
    })
    upsertUser(user)
    createForm.username = ''
    createForm.password = ''
    createForm.role = 'user'
    createForm.pages = []
    successMessage.value = '用户已创建。'
  })
}

async function changeRole(user: ManagedUser, role: UserRole): Promise<void> {
  await mutate(async () => {
    upsertUser(await apiRequest<ManagedUser>(`/users/${encodeURIComponent(user.username)}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }))
  })
}

async function changeStatus(user: ManagedUser): Promise<void> {
  await mutate(async () => {
    upsertUser(await apiRequest<ManagedUser>(`/users/${encodeURIComponent(user.username)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !user.is_active }),
    }))
  })
}

async function changePages(user: ManagedUser): Promise<void> {
  await mutate(async () => {
    upsertUser(await apiRequest<ManagedUser>(`/users/${encodeURIComponent(user.username)}/pages`, {
      method: 'PATCH',
      body: JSON.stringify({ pages: user.pages }),
    }))
  })
}

async function changePassword(user: ManagedUser): Promise<void> {
  const form = passwordForms[user.username] || { password: '', confirmation: '' }
  if (!form.password || form.password !== form.confirmation) {
    errorMessage.value = '两次输入的新密码不一致。'
    return
  }
  await mutate(async () => {
    upsertUser(await apiRequest<ManagedUser>(`/users/${encodeURIComponent(user.username)}/password`, {
      method: 'PATCH',
      body: JSON.stringify({ password: form.password }),
    }))
    form.password = ''
    form.confirmation = ''
    successMessage.value = '密码已重置。'
  })
}

function passwordForm(username: string): { password: string; confirmation: string } {
  passwordForms[username] ||= { password: '', confirmation: '' }
  return passwordForms[username]
}

async function mutate(operation: () => Promise<void>): Promise<void> {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await operation()
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '用户操作失败。'
  }
}
</script>

<template>
  <section class="page-panel" aria-labelledby="users-title">
    <header class="page-heading">
      <div>
        <span class="eyebrow">ACCESS CONTROL</span>
        <h1 id="users-title">权限管理</h1>
        <p>查看系统账户、角色和当前页面授权。</p>
      </div>
      <span class="count-chip">{{ total }} 个账户</span>
    </header>

    <p v-if="loading" role="status" class="state-card">正在加载用户…</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-if="successMessage" role="status" class="state-card">{{ successMessage }}</p>

    <form class="form-card" aria-label="新增用户" @submit.prevent="createUser">
      <h2>新增用户</h2>
      <label>
        用户名
        <input v-model="createForm.username" name="username" autocomplete="off" />
      </label>
      <label>
        初始密码
        <input v-model="createForm.password" name="new_password" type="password" autocomplete="new-password" />
      </label>
      <label>
        角色
        <select v-model="createForm.role" name="role">
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </select>
      </label>
      <fieldset :disabled="createForm.role === 'admin'">
        <legend>页面授权</legend>
        <label v-for="page in pageOptions" :key="page.code" class="checkbox-line">
          <input v-model="createForm.pages" type="checkbox" :value="page.code" />
          {{ page.label }}
        </label>
        <p v-if="createForm.role === 'admin'">管理员默认拥有全部页面权限。</p>
      </fieldset>
      <button type="submit">创建用户</button>
    </form>

    <p v-if="!loading && users.length === 0" class="state-card">暂无用户</p>
    <div v-else-if="users.length > 0" class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">用户名</th>
            <th scope="col">角色</th>
            <th scope="col">状态</th>
            <th scope="col">页面权限</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in users" :key="item.id">
            <td><strong>{{ item.username }}</strong></td>
            <td>
              <select
                :aria-label="`${item.username} 角色`"
                :value="item.role"
                @change="changeRole(item, ($event.target as HTMLSelectElement).value as UserRole)"
              >
                <option value="user">普通用户</option>
                <option value="admin">管理员</option>
              </select>
            </td>
            <td><span class="status-pill" :class="{ muted: !item.is_active }">{{ item.is_active ? '启用' : '停用' }}</span></td>
            <td>
              <label v-for="page in pageOptions" :key="page.code" class="checkbox-line">
                <input
                  v-model="item.pages"
                  type="checkbox"
                  :value="page.code"
                  :disabled="item.role === 'admin'"
                  @change="changePages(item)"
                />
                {{ page.label }}
              </label>
              <p v-if="item.role === 'admin'">全部页面</p>
              <p v-else-if="effectivePages(item).length === 0">无</p>
            </td>
            <td>
              <button type="button" @click="changeStatus(item)">{{ item.is_active ? '停用' : '启用' }}</button>
              <form class="inline-form" :aria-label="`${item.username} 重置密码`" @submit.prevent="changePassword(item)">
                <input
                  v-model="passwordForm(item.username).password"
                  :aria-label="`${item.username} 新密码`"
                  type="password"
                  autocomplete="new-password"
                />
                <input
                  v-model="passwordForm(item.username).confirmation"
                  :aria-label="`${item.username} 确认新密码`"
                  type="password"
                  autocomplete="new-password"
                />
                <button type="submit">重置密码</button>
              </form>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
