<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, apiRequest } from '../../api/client'
import type { CrawlTask, Page, TaskStatus } from '../../api/types'
import StatusTag from '../../components/StatusTag.vue'
import { formatBeijingTime } from '../../utils/time'

const filters = reactive({
  status: '',
  ruleId: '',
  triggerType: '',
  startedFrom: '',
  startedTo: '',
  page: 1,
  pageSize: 20,
})
const loading = ref(true)
const errorMessage = ref('')
const tasks = ref<CrawlTask[]>([])
const total = ref(0)

onMounted(() => {
  void loadTasks()
})

async function loadTasks(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const query: Record<string, string | number> = { page: filters.page, page_size: filters.pageSize }
    if (filters.status) query.status = filters.status
    if (filters.ruleId) query.rule_id = filters.ruleId
    if (filters.triggerType) query.trigger_type = filters.triggerType
    if (filters.startedFrom) query.started_from = filters.startedFrom
    if (filters.startedTo) query.started_to = filters.startedTo
    const response = await apiRequest<Page<CrawlTask>>('/tasks', { query })
    tasks.value = response.items
    total.value = response.total
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '任务列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function submitFilters(): Promise<void> {
  filters.page = 1
  await loadTasks()
}

async function clearFilters(): Promise<void> {
  filters.status = ''
  filters.ruleId = ''
  filters.triggerType = ''
  filters.startedFrom = ''
  filters.startedTo = ''
  filters.page = 1
  await loadTasks()
}

async function changePage(page: number): Promise<void> {
  filters.page = page
  await loadTasks()
}
</script>

<template>
  <section class="page-panel" aria-labelledby="tasks-title">
    <div class="page-heading">
      <div>
        <span class="eyebrow">TASK CENTER</span>
        <h1 id="tasks-title">任务中心</h1>
      </div>
    </div>

    <form class="filter-grid" aria-label="任务筛选" @submit.prevent="submitFilters">
      <label>
        状态
        <select v-model="filters.status" name="status">
          <option value="">全部</option>
          <option value="pending">等待</option>
          <option value="running">运行</option>
          <option value="succeeded">成功</option>
          <option value="failed">失败</option>
        </select>
      </label>
      <label>
        规则 ID
        <input v-model="filters.ruleId" name="rule_id" inputmode="numeric" />
      </label>
      <label>
        触发方式
        <select v-model="filters.triggerType" name="trigger_type">
          <option value="">全部</option>
          <option value="manual">手工</option>
          <option value="schedule">定时</option>
        </select>
      </label>
      <label>
        开始时间起
        <input v-model="filters.startedFrom" name="started_from" type="date" />
      </label>
      <label>
        开始时间止
        <input v-model="filters.startedTo" name="started_to" type="date" />
      </label>
      <div class="filter-actions">
        <button type="submit">筛选</button>
        <button type="button" @click="clearFilters">清空筛选</button>
      </div>
    </form>

    <p v-if="loading" role="status" class="state-card">正在加载任务</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-else-if="tasks.length === 0" class="state-card">暂无任务</p>
    <table v-else class="data-table">
      <thead>
        <tr>
          <th>任务</th>
          <th>状态</th>
          <th>规则</th>
          <th>触发方式</th>
          <th>进度</th>
          <th>开始时间</th>
          <th>结束时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in tasks" :key="task.id">
          <td><RouterLink :to="{ name: 'task-detail', params: { taskId: task.id } }">#{{ task.id }}</RouterLink></td>
          <td><StatusTag :status="task.status as TaskStatus" /></td>
          <td>{{ task.rule_id }}</td>
          <td>{{ task.trigger_type === 'manual' ? '手工' : '定时' }}</td>
          <td>{{ task.progress.processed }} / {{ task.progress.discovered }}</td>
          <td>{{ formatBeijingTime(task.started_at) }}</td>
          <td>{{ formatBeijingTime(task.finished_at) }}</td>
        </tr>
      </tbody>
    </table>

    <nav v-if="total > filters.pageSize" class="pagination" aria-label="任务分页">
      <button type="button" :disabled="filters.page <= 1" @click="changePage(filters.page - 1)">上一页</button>
      <span>第 {{ filters.page }} 页 / 共 {{ total }} 条</span>
      <button type="button" :disabled="filters.page * filters.pageSize >= total" @click="changePage(filters.page + 1)">
        下一页
      </button>
    </nav>
  </section>
</template>
