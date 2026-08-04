<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError, apiRequest } from '../../api/client'
import type { CrawlTask, CrawlTaskItem, Page, TaskLog, TaskStatus } from '../../api/types'
import StatusTag from '../../components/StatusTag.vue'
import { formatBeijingTime } from '../../utils/time'
import { createTaskPolling } from './use-task-polling'

const props = defineProps<{ taskId: number | string }>()
const loading = ref(true)
const errorMessage = ref('')
const task = ref<CrawlTask | null>(null)
const items = ref<CrawlTaskItem[]>([])
const logs = ref<TaskLog[]>([])
const cancelling = ref(false)
const canCancel = computed(() => task.value?.status === 'pending' || task.value?.status === 'running')

const polling = createTaskPolling(async () => {
  await loadTask()
  return { status: task.value?.status || 'failed' }
})

onMounted(() => {
  void loadAll().then(() => {
    if (canCancel.value) void polling.start()
  })
})
onBeforeUnmount(() => polling.stop())

async function loadTask(): Promise<void> {
  task.value = await apiRequest<CrawlTask>(`/tasks/${props.taskId}`)
}

async function loadAll(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [taskResponse, itemResponse, logResponse] = await Promise.all([
      apiRequest<CrawlTask>(`/tasks/${props.taskId}`),
      apiRequest<Page<CrawlTaskItem>>(`/tasks/${props.taskId}/items`),
      apiRequest<Page<TaskLog>>(`/tasks/${props.taskId}/logs`),
    ])
    task.value = taskResponse
    items.value = itemResponse.items
    logs.value = logResponse.items
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '任务详情加载失败。'
  } finally {
    loading.value = false
  }
}

async function cancelTask(): Promise<void> {
  if (!canCancel.value || cancelling.value) return
  cancelling.value = true
  try {
    task.value = await apiRequest<CrawlTask>(`/tasks/${props.taskId}/cancel`, { method: 'POST' })
  } finally {
    cancelling.value = false
  }
}
</script>

<template>
  <section class="page-panel" aria-labelledby="task-detail-title">
    <p v-if="loading" role="status" class="state-card">正在加载任务详情</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <div v-else-if="task">
      <div class="page-heading">
        <div>
          <span class="eyebrow">TASK #{{ task.id }}</span>
          <h1 id="task-detail-title">采集任务详情</h1>
        </div>
        <button v-if="canCancel" type="button" :disabled="cancelling" @click="cancelTask">取消任务</button>
      </div>
      <StatusTag :status="task.status as TaskStatus" />
      <dl class="metadata-grid">
        <div><dt>进度</dt><dd>{{ task.progress.processed }} / {{ task.progress.discovered }}</dd></div>
        <div><dt>成功</dt><dd>{{ task.counts.success }}</dd></div>
        <div><dt>重复</dt><dd>{{ task.counts.duplicate }}</dd></div>
        <div><dt>过滤</dt><dd>{{ task.counts.filtered }}</dd></div>
        <div><dt>失败</dt><dd>{{ task.counts.failed }}</dd></div>
        <div><dt>总处理</dt><dd>{{ task.counts.total_terminal_items }}</dd></div>
      </dl>

      <h2>任务明细</h2>
      <table class="data-table">
        <thead><tr><th>候选 URL</th><th>状态</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>{{ item.candidate_url }}</td>
            <td>{{ item.status }}</td>
            <td>{{ item.reason_code }} {{ item.reason_message }}</td>
          </tr>
        </tbody>
      </table>

      <h2>任务日志</h2>
      <table class="data-table">
        <thead><tr><th>时间</th><th>级别</th><th>消息</th></tr></thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id">
            <td>{{ formatBeijingTime(log.created_at) }}</td>
            <td>{{ log.level }}</td>
            <td>{{ log.message }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
