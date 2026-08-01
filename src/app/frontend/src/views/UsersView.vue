<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'
import type { PageCode, UserRole } from '../stores/auth'

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
    <p v-else-if="users.length === 0" class="state-card">暂无用户</p>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">用户名</th>
            <th scope="col">角色</th>
            <th scope="col">状态</th>
            <th scope="col">页面权限</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in users" :key="item.id">
            <td><strong>{{ item.username }}</strong></td>
            <td>{{ item.role === 'admin' ? '管理员' : '普通用户' }}</td>
            <td><span class="status-pill" :class="{ muted: !item.is_active }">{{ item.is_active ? '启用' : '停用' }}</span></td>
            <td>{{ item.role === 'admin' ? '全部页面' : item.pages.join('、') || '无' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
