<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../../api/client'
import type {
  AnalysisTaskLogPage,
  AnalysisTaskPage,
  AnalysisTaskSummary,
  PolicyComparisonReport,
  WordFrequencyResult,
  WordRelationResult,
} from '../../api/types'
import { formatBeijingTime } from '../../utils/time'
import WordCloudChart from '../../components/charts/WordCloudChart.vue'
import RelationGraphChart from '../../components/charts/RelationGraphChart.vue'

type Tab = 'frequency' | 'cloud' | 'graph'

const route = useRoute()
const router = useRouter()

const activeTab = ref<Tab>('frequency')
const task = ref<AnalysisTaskSummary | null>(null)
const words = ref<WordFrequencyResult | null>(null)
const relations = ref<WordRelationResult | null>(null)
const logs = ref<AnalysisTaskLogPage | null>(null)
const comparison = ref<PolicyComparisonReport | null>(null)
const history = ref<AnalysisTaskSummary[]>([])
const loadError = ref('')
let pollTimer: ReturnType<typeof setTimeout> | null = null

const TERMINAL = new Set(['succeeded', 'failed'])

const taskId = computed(() => {
  const value = route.query.taskId
  return typeof value === 'string' && value ? Number(value) : null
})

const isRunning = computed(
  () => task.value?.status === 'pending' || task.value?.status === 'running',
)

async function loadTask(): Promise<void> {
  if (taskId.value === null) return
  try {
    task.value = await apiRequest<AnalysisTaskSummary>(`/analysis/tasks/${taskId.value}`)
    loadError.value = ''
  } catch (error) {
    loadError.value = error instanceof ApiError ? error.message : '加载任务失败。'
  }
}

async function loadResults(): Promise<void> {
  if (taskId.value === null || task.value?.status !== 'succeeded') return
  try {
    if (task.value.task_type === 'policy_comparison') {
      comparison.value = await apiRequest<PolicyComparisonReport>(
        `/analysis/tasks/${taskId.value}/comparison-report`,
      )
      logs.value = await apiRequest<AnalysisTaskLogPage>(`/analysis/tasks/${taskId.value}/logs`)
      return
    }
    words.value = await apiRequest<WordFrequencyResult>(
      `/analysis/tasks/${taskId.value}/words?top=50`,
    )
    relations.value = await apiRequest<WordRelationResult>(
      `/analysis/tasks/${taskId.value}/relations?top=50`,
    )
    logs.value = await apiRequest<AnalysisTaskLogPage>(
      `/analysis/tasks/${taskId.value}/logs`,
    )
  } catch (error) {
    loadError.value = error instanceof ApiError ? error.message : '加载结果失败。'
  }
}

async function loadHistory(): Promise<void> {
  try {
    const page = await apiRequest<AnalysisTaskPage>('/analysis/tasks?page=1&page_size=20')
    history.value = page.items
  } catch {
    history.value = []
  }
}

function stopPoll(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(): void {
  stopPoll()
  if (task.value && !TERMINAL.has(task.value.status)) {
    pollTimer = setTimeout(() => void pollTick(), 2000)
  }
}

async function pollTick(): Promise<void> {
  await loadTask()
  if (task.value && TERMINAL.has(task.value.status)) {
    if (task.value.status === 'succeeded') {
      await loadResults()
    }
    return
  }
  schedulePoll()
}

async function initialize(): Promise<void> {
  await Promise.all([
    loadHistory(),
    (async () => {
      await loadTask()
      if (task.value && task.value.status === 'succeeded') {
        await loadResults()
      } else if (task.value && !TERMINAL.has(task.value.status)) {
        schedulePoll()
      }
    })(),
  ])
}

watch(taskId, () => {
  stopPoll()
  task.value = null
  words.value = null
  relations.value = null
  comparison.value = null
  logs.value = null
  loadError.value = ''
  void initialize()
})

onMounted(() => void initialize())
onBeforeUnmount(stopPoll)

function selectHistory(id: number): void {
  void router.push({ name: 'analysis', query: { taskId: String(id) } })
}

function statusText(status: string): string {
  const map: Record<string, string> = {
    pending: '排队中',
    running: '分析中',
    succeeded: '已完成',
    failed: '失败',
  }
  return map[status] ?? status
}

function taskTypeText(taskType: string): string {
  return taskType === 'policy_comparison' ? '政策比对' : '分词分析'
}

function policyTitle(id: number): string {
  return comparison.value?.policies.find((item) => item.id === id)?.title ?? `政策 #${id}`
}

async function sortWords(by: 'frequency' | 'tfidf'): Promise<void> {
  if (taskId.value === null) return
  words.value = await apiRequest<WordFrequencyResult>(
    `/analysis/tasks/${taskId.value}/words?top=50&sort_by=${by}`,
  )
}
</script>

<template>
  <section class="page-panel" aria-labelledby="analysis-title">
    <div class="page-heading">
      <div>
        <span class="eyebrow">POLICY ANALYSIS</span>
        <h1 id="analysis-title">政策分析</h1>
      </div>
    </div>

    <p v-if="loadError" role="alert" class="state-card error-state">{{ loadError }}</p>

    <div v-if="task" class="analysis-task">
      <h2>任务 #{{ task.id }}</h2>
      <dl class="task-meta">
        <dt>分析类型</dt>
        <dd>{{ taskTypeText(task.task_type) }}</dd>
        <dt>状态</dt>
        <dd>{{ statusText(task.status) }}</dd>
        <dt>政策数</dt>
        <dd>{{ task.policy_count }}</dd>
        <dt>创建时间</dt>
        <dd>{{ formatBeijingTime(task.created_at) }}</dd>
        <template v-if="task.finished_at">
          <dt>完成时间</dt>
          <dd>{{ formatBeijingTime(task.finished_at) }}</dd>
        </template>
      </dl>
      <p v-if="task.status === 'failed' && task.error_summary" class="state-card error-state">
        {{ task.error_summary }}
      </p>
      <p v-if="isRunning" class="state-card">分析进行中，请稍候…</p>

      <div v-if="task.status === 'succeeded' && words" class="analysis-tabs">
        <div class="tab-bar" role="tablist">
          <button
            role="tab"
            :aria-selected="activeTab === 'frequency'"
            :class="{ active: activeTab === 'frequency' }"
            @click="activeTab = 'frequency'"
          >
            词频排行
          </button>
          <button
            role="tab"
            :aria-selected="activeTab === 'cloud'"
            :class="{ active: activeTab === 'cloud' }"
            @click="activeTab = 'cloud'"
          >
            词云
          </button>
          <button
            role="tab"
            :aria-selected="activeTab === 'graph'"
            :class="{ active: activeTab === 'graph' }"
            @click="activeTab = 'graph'"
          >
            关键词关系图
          </button>
        </div>

        <div v-if="activeTab === 'frequency'" class="tab-panel">
          <div class="sort-actions">
            <button type="button" @click="sortWords('frequency')">按频次排序</button>
            <button type="button" @click="sortWords('tfidf')">按 TF-IDF 排序</button>
          </div>
          <table class="data-table">
            <thead>
              <tr>
                <th>关键词</th>
                <th>出现次数</th>
                <th>TF-IDF</th>
                <th>出现篇数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in words.items" :key="item.word">
                <td>{{ item.word }}</td>
                <td>{{ item.frequency }}</td>
                <td>{{ item.tfidf.toFixed(3) }}</td>
                <td>{{ item.doc_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="activeTab === 'cloud'" class="tab-panel">
          <WordCloudChart :items="words.items" />
        </div>

        <div v-if="activeTab === 'graph'" class="tab-panel">
          <RelationGraphChart v-if="relations" :result="relations" />
          <p v-else class="state-card">无关系数据</p>
        </div>
      </div>

      <article v-if="task.status === 'succeeded' && comparison" class="comparison-report">
        <h2>政策差异分析报告</h2>
        <p class="state-card">{{ comparison.summary }}</p>

        <section>
          <h3>政策概览</h3>
          <table class="data-table">
            <thead><tr><th>政策</th><th>发布部门</th><th>发布时间</th><th>核心关键词</th></tr></thead>
            <tbody>
              <tr v-for="policy in comparison.policies" :key="policy.id">
                <td>{{ policy.title }}</td>
                <td>{{ policy.publisher }}</td>
                <td>{{ formatBeijingTime(policy.published_at) }}</td>
                <td>{{ policy.top_keywords.join('、') || '无' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section>
          <h3>共同关注点</h3>
          <p>{{ comparison.common_keywords.join('、') || '未发现全部政策共同出现的显著关键词。' }}</p>
        </section>

        <section>
          <h3>两两差异</h3>
          <div v-for="pair in comparison.pair_differences" :key="`${pair.left_policy_id}-${pair.right_policy_id}`" class="state-card">
            <h4>{{ policyTitle(pair.left_policy_id) }} vs {{ policyTitle(pair.right_policy_id) }}</h4>
            <p>文本特征相似度：{{ (pair.similarity * 100).toFixed(1) }}%</p>
            <p><strong>共同重点：</strong>{{ pair.shared_keywords.join('、') || '无' }}</p>
            <p><strong>前者独有重点：</strong>{{ pair.left_only_keywords.join('、') || '无' }}</p>
            <p><strong>后者独有重点：</strong>{{ pair.right_only_keywords.join('、') || '无' }}</p>
          </div>
        </section>
      </article>
    </div>

    <div class="analysis-history">
      <h2>历史分析任务</h2>
      <table v-if="history.length > 0" class="data-table">
        <thead>
          <tr>
            <th>任务</th>
            <th>类型</th>
            <th>状态</th>
            <th>政策数</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in history" :key="item.id">
            <td>
              <button class="link-button" @click="selectHistory(item.id)">#{{ item.id }}</button>
            </td>
            <td>{{ taskTypeText(item.task_type) }}</td>
            <td>{{ statusText(item.status) }}</td>
            <td>{{ item.policy_count }}</td>
            <td>{{ formatBeijingTime(item.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="state-card">暂无历史任务，前往政策数据库选择政策开始分析。</p>
    </div>
  </section>
</template>
