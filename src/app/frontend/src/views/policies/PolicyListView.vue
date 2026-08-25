<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest } from '../../api/client'
import type { CreateAnalysisTaskResponse, Page, PolicyFilterOptions, PolicySummary } from '../../api/types'
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
const filterErrorMessage = ref('')
const policies = ref<PolicySummary[]>([])
const filterOptions = ref<PolicyFilterOptions>({ publishers: [], categories: [], sources: [] })
const total = ref(0)
const selectedIds = ref<number[]>([])
const submitting = ref(false)
const submitError = ref('')
let policyRequestId = 0

const allSelected = computed(
  () =>
    policies.value.length > 0 &&
    policies.value.every((policy) => selectedIds.value.includes(policy.id)),
)

function toggleAll(): void {
  const currentIds = policies.value.map((policy) => policy.id)
  if (currentIds.every((id) => selectedIds.value.includes(id))) {
    selectedIds.value = selectedIds.value.filter((id) => !currentIds.includes(id))
  } else {
    selectedIds.value = [...new Set([...selectedIds.value, ...currentIds])]
  }
}

function toggleOne(id: number): void {
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((value) => value !== id)
  } else {
    selectedIds.value = [...selectedIds.value, id]
  }
}

async function startAnalysis(): Promise<void> {
  if (selectedIds.value.length === 0) {
    submitError.value = '请至少选择一篇政策。'
    return
  }
  submitError.value = ''
  submitting.value = true
  try {
    const result = await apiRequest<CreateAnalysisTaskResponse>('/analysis/tasks', {
      method: 'POST',
      body: JSON.stringify({ policy_ids: selectedIds.value }),
    })
    selectedIds.value = []
    await router.push({ name: 'analysis', query: { taskId: String(result.task_id) } })
  } catch (error) {
    submitError.value = error instanceof ApiError ? error.message : '创建分析任务失败。'
  } finally {
    submitting.value = false
  }
}

async function startComparison(): Promise<void> {
  if (selectedIds.value.length < 2) {
    submitError.value = '政策比对至少需要选择两篇政策。'
    return
  }
  submitError.value = ''
  submitting.value = true
  try {
    const result = await apiRequest<CreateAnalysisTaskResponse>('/analysis/comparison-tasks', {
      method: 'POST',
      body: JSON.stringify({ policy_ids: selectedIds.value }),
    })
    selectedIds.value = []
    await router.push({ name: 'analysis', query: { taskId: String(result.task_id) } })
  } catch (error) {
    submitError.value = error instanceof ApiError ? error.message : '创建政策比对任务失败。'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void Promise.all([loadFilterOptions(), loadPolicies()])
})

watch(
  () => route.query,
  (query) => {
    Object.assign(form, fromRouteQuery(query))
    void loadPolicies()
  },
)

async function loadFilterOptions(): Promise<void> {
  filterErrorMessage.value = ''
  try {
    filterOptions.value = await apiRequest<PolicyFilterOptions>('/policies/filters')
  } catch (error) {
    filterErrorMessage.value = error instanceof ApiError ? error.message : '筛选选项加载失败。'
  }
}

async function loadPolicies(): Promise<void> {
  const requestId = ++policyRequestId
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await apiRequest<Page<PolicySummary> & { sort_by: string; sort_order: string }>(
      '/policies',
      { query: toPolicyApiQuery(form) },
    )
    if (requestId !== policyRequestId) return
    policies.value = response.items
    total.value = response.total
  } catch (error) {
    if (requestId !== policyRequestId) return
    errorMessage.value = error instanceof ApiError ? error.message : '政策列表加载失败。'
  } finally {
    if (requestId === policyRequestId) loading.value = false
  }
}

async function submitFilters(): Promise<void> {
  form.page = 1
  await syncRoute()
}

async function clearFilters(): Promise<void> {
  Object.assign(form, defaultPolicyQuery())
  await syncRoute()
}

async function changePage(nextPage: number): Promise<void> {
  form.page = nextPage
  await syncRoute()
}

async function changeSort(sortBy: PolicyQueryForm['sortBy']): Promise<void> {
  if (form.sortBy === sortBy) {
    form.sortOrder = form.sortOrder === 'asc' ? 'desc' : 'asc'
  } else {
    form.sortBy = sortBy
    form.sortOrder = 'desc'
  }
  await syncRoute()
}

async function syncRoute(): Promise<void> {
  const target = { name: 'policies', query: toPolicyApiQuery(form) }
  if (router.resolve(target).fullPath === route.fullPath) {
    await loadPolicies()
    return
  }
  await router.push(target)
}

function sortAria(sortBy: PolicyQueryForm['sortBy']): 'ascending' | 'descending' | 'none' {
  if (form.sortBy !== sortBy) return 'none'
  return form.sortOrder === 'asc' ? 'ascending' : 'descending'
}

function sortDescription(sortBy: PolicyQueryForm['sortBy']): string {
  if (form.sortBy !== sortBy) return ''
  return form.sortOrder === 'asc' ? '升序（最早优先）' : '降序（最新优先）'
}

function sortButtonLabel(label: string, sortBy: PolicyQueryForm['sortBy']): string {
  if (form.sortBy !== sortBy) return `${label}，点击切换为降序（最新优先）`
  const current = sortDescription(sortBy)
  const next = form.sortOrder === 'asc' ? '降序（最新优先）' : '升序（最早优先）'
  return `${label}，当前${current}，点击切换为${next}`
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
        标题关键词
        <input v-model="form.keyword" name="keyword" />
      </label>
      <label>
        正文全文检索
        <input v-model="form.fullText" name="full_text" />
      </label>
      <label>
        发布部门
        <select v-model="form.publisher" name="publisher">
          <option value="">全部</option>
          <option
            v-if="form.publisher && !filterOptions.publishers.includes(form.publisher)"
            :value="form.publisher"
          >
            {{ form.publisher }}（当前筛选）
          </option>
          <option v-for="publisher in filterOptions.publishers" :key="publisher" :value="publisher">
            {{ publisher }}
          </option>
        </select>
      </label>
      <label>
        政策类别
        <select v-model="form.categoryId" name="category_id">
          <option value="">全部</option>
          <option
            v-if="form.categoryId && !filterOptions.categories.some((category) => String(category.id) === form.categoryId)"
            :value="form.categoryId"
          >
            当前类别 #{{ form.categoryId }}
          </option>
          <option v-for="category in filterOptions.categories" :key="category.id" :value="String(category.id)">
            {{ category.name }}
          </option>
        </select>
      </label>
      <label>
        政策来源
        <select v-model="form.sourceId" name="source_id">
          <option value="">全部</option>
          <option
            v-if="form.sourceId && !filterOptions.sources.some((source) => String(source.id) === form.sourceId)"
            :value="form.sourceId"
          >
            当前来源 #{{ form.sourceId }}
          </option>
          <option v-for="source in filterOptions.sources" :key="source.id" :value="String(source.id)">
            {{ source.name }}
          </option>
        </select>
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

    <div class="analysis-bar">
      <button
        type="button"
        :disabled="submitting || selectedIds.length === 0"
        @click="startAnalysis"
      >
        分词分析<template v-if="selectedIds.length > 0">（已选 {{ selectedIds.length }} 篇）</template>
      </button>
      <button
        type="button"
        :disabled="submitting || selectedIds.length < 2"
        @click="startComparison"
      >
        政策比对<template v-if="selectedIds.length > 0">（已选 {{ selectedIds.length }} 篇）</template>
      </button>
      <span v-if="submitError" role="alert" class="filter-error">{{ submitError }}</span>
    </div>

    <p v-if="filterErrorMessage" role="alert" class="filter-error">{{ filterErrorMessage }}</p>

    <p v-if="loading" role="status" class="state-card">正在加载政策</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-else-if="policies.length === 0" class="state-card">暂无政策</p>
    <table v-else class="data-table">
      <thead>
        <tr>
          <th class="select-col">
            <input
              type="checkbox"
              :checked="allSelected"
              :aria-label="allSelected ? '取消全选当前页' : '全选当前页'"
              @change="toggleAll"
            />
          </th>
          <th>标题</th>
          <th>发布部门</th>
          <th>类别</th>
          <th :aria-sort="sortAria('published_at')">
            <button
              type="button"
              :aria-label="sortButtonLabel('发布时间', 'published_at')"
              @click="changeSort('published_at')"
            >
              发布时间 <span v-if="form.sortBy === 'published_at'" class="sort-hint">{{ sortDescription('published_at') }}</span>
            </button>
          </th>
          <th :aria-sort="sortAria('last_crawled_at')">
            <button
              type="button"
              :aria-label="sortButtonLabel('最近抓取时间', 'last_crawled_at')"
              @click="changeSort('last_crawled_at')"
            >
              最近抓取时间 <span v-if="form.sortBy === 'last_crawled_at'" class="sort-hint">{{ sortDescription('last_crawled_at') }}</span>
            </button>
          </th>
          <th>来源</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="policy in policies" :key="policy.id">
          <td class="select-col">
            <input
              type="checkbox"
              :checked="selectedIds.includes(policy.id)"
              :aria-label="`选择 ${policy.title}`"
              @change="toggleOne(policy.id)"
            />
          </td>
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
