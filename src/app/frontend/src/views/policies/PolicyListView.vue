<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest } from '../../api/client'
import type { Page, PolicySummary } from '../../api/types'
import { formatBeijingTime } from '../../utils/time'
import {
  defaultPolicyQuery,
  fromRouteQuery,
  toPolicyApiQuery,
  type PolicyQueryForm,
} from './policy-query'

const route = useRoute()
const router = useRouter()
const form = reactive<PolicyQueryForm>(fromRouteQuery(route.query))
const loading = ref(true)
const errorMessage = ref('')
const policies = ref<PolicySummary[]>([])
const total = ref(0)

onMounted(() => {
  void loadPolicies()
})

async function loadPolicies(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await apiRequest<Page<PolicySummary> & { sort_by: string; sort_order: string }>(
      '/policies',
      { query: toPolicyApiQuery(form) },
    )
    policies.value = response.items
    total.value = response.total
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '政策列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function submitFilters(): Promise<void> {
  form.page = 1
  await syncRoute()
  await loadPolicies()
}

async function clearFilters(): Promise<void> {
  Object.assign(form, defaultPolicyQuery())
  await syncRoute()
  await loadPolicies()
}

async function changePage(nextPage: number): Promise<void> {
  form.page = nextPage
  await syncRoute()
  await loadPolicies()
}

async function changeSort(sortBy: PolicyQueryForm['sortBy']): Promise<void> {
  if (form.sortBy === sortBy) {
    form.sortOrder = form.sortOrder === 'asc' ? 'desc' : 'asc'
  } else {
    form.sortBy = sortBy
    form.sortOrder = 'desc'
  }
  await syncRoute()
  await loadPolicies()
}

async function syncRoute(): Promise<void> {
  await router.replace({ name: 'policies', query: toPolicyApiQuery(form) })
}
</script>

<template>
  <section class="page-panel" aria-labelledby="policies-title">
    <div class="page-heading">
      <div>
        <span class="eyebrow">POLICY DATABASE</span>
        <h1 id="policies-title">政策数据库</h1>
      </div>
    </div>

    <form class="filter-grid" aria-label="政策筛选" @submit.prevent="submitFilters">
      <label>
        关键词
        <input v-model="form.keyword" name="keyword" />
      </label>
      <label>
        发布部门
        <input v-model="form.publisher" name="publisher" />
      </label>
      <label>
        类别 ID
        <input v-model="form.categoryId" name="category_id" inputmode="numeric" />
      </label>
      <label>
        来源 ID
        <input v-model="form.sourceId" name="source_id" inputmode="numeric" />
      </label>
      <label>
        发布时间起
        <input v-model="form.publishedFrom" name="published_from" type="date" />
      </label>
      <label>
        发布时间止
        <input v-model="form.publishedTo" name="published_to" type="date" />
      </label>
      <div class="filter-actions">
        <button type="submit">筛选</button>
        <button type="button" @click="clearFilters">清空筛选</button>
      </div>
    </form>

    <p v-if="loading" role="status" class="state-card">正在加载政策</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-else-if="policies.length === 0" class="state-card">暂无政策</p>
    <table v-else class="data-table">
      <thead>
        <tr>
          <th>标题</th>
          <th>发布部门</th>
          <th>类别</th>
          <th><button type="button" @click="changeSort('published_at')">发布时间</button></th>
          <th><button type="button" @click="changeSort('last_crawled_at')">最近抓取时间</button></th>
          <th>来源</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="policy in policies" :key="policy.id">
          <td>
            <RouterLink :to="{ name: 'policy-detail', params: { policyId: policy.id } }">
              {{ policy.title }}
            </RouterLink>
          </td>
          <td>{{ policy.publisher }}</td>
          <td>{{ policy.category.name }}</td>
          <td>{{ formatBeijingTime(policy.published_at) }}</td>
          <td>{{ formatBeijingTime(policy.last_crawled_at) }}</td>
          <td>{{ policy.source.name }}</td>
        </tr>
      </tbody>
    </table>

    <nav v-if="total > form.pageSize" class="pagination" aria-label="政策分页">
      <button type="button" :disabled="form.page <= 1" @click="changePage(form.page - 1)">上一页</button>
      <span>第 {{ form.page }} 页 / 共 {{ total }} 条</span>
      <button type="button" :disabled="form.page * form.pageSize >= total" @click="changePage(form.page + 1)">
        下一页
      </button>
    </nav>
  </section>
</template>
