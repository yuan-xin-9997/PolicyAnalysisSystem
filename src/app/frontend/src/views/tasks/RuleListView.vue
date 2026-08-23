<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, apiRequest } from '../../api/client'
import type { CollectionRule, PolicyCategory, SourceSummary } from '../../api/types'
import { useAuthStore } from '../../stores/auth'
import { formatBeijingTime } from '../../utils/time'
import RuleFormDialog from './RuleFormDialog.vue'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const loading = ref(true)
const errorMessage = ref('')
const rules = ref<CollectionRule[]>([])
const sources = ref<SourceSummary[]>([])
const categories = ref<PolicyCategory[]>([])
const editingRuleId = ref<number | null>(null)

const editingRule = computed(() => rules.value.find((rule) => rule.id === editingRuleId.value) ?? null)

onMounted(() => {
  void loadAll()
})

async function loadAll(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [ruleItems, sourceItems, categoryItems] = await Promise.all([
      apiRequest<CollectionRule[]>('/collection-rules'),
      apiRequest<SourceSummary[]>('/sources'),
      apiRequest<PolicyCategory[]>('/policy-categories'),
    ])
    rules.value = ruleItems
    sources.value = sourceItems
    categories.value = categoryItems
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '采集规则加载失败。'
  } finally {
    loading.value = false
  }
}

function upsertRule(rule: CollectionRule): void {
  const index = rules.value.findIndex((item) => item.id === rule.id)
  if (index >= 0) rules.value[index] = rule
  else rules.value = [rule, ...rules.value]
  editingRuleId.value = null
}

function startEditing(rule: CollectionRule): void {
  editingRuleId.value = rule.id
}
</script>

<template>
  <section class="page-panel" aria-labelledby="rules-title">
    <div class="page-heading">
      <div>
        <span class="eyebrow">COLLECTION RULES</span>
        <h1 id="rules-title">采集规则</h1>
      </div>
      <RouterLink to="/tasks">返回任务中心</RouterLink>
    </div>

    <p v-if="loading" role="status" class="state-card">正在加载采集配置</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <template v-else>
      <RuleFormDialog
        v-if="isAdmin"
        :key="editingRule?.id ?? 'create'"
        :sources="sources"
        :categories="categories"
        :rule="editingRule"
        @saved="upsertRule"
        @cancelled="editingRuleId = null"
      />
      <p v-else class="state-card">普通用户仅可查看规则，不能新增或编辑。</p>

      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>规则</th>
            <th>来源</th>
            <th>类别</th>
            <th>包含词</th>
            <th>排除词</th>
            <th>历史窗口</th>
            <th>触发方式</th>
            <th>定时配置</th>
            <th>状态</th>
            <th>更新时间</th>
            <th v-if="isAdmin">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in rules" :key="rule.id">
            <td>{{ rule.id }}</td>
            <td>{{ rule.name }}</td>
            <td>{{ rule.source.name }}</td>
            <td>{{ rule.category.name }}</td>
            <td>{{ rule.include_keywords.join('、') }}</td>
            <td>{{ rule.exclude_keywords.join('、') || '—' }}</td>
            <td>{{ rule.history_years }} 年</td>
            <td>{{ rule.trigger_mode === 'schedule' ? '定时运行' : '手工触发' }}</td>
            <td>
              <template v-if="rule.trigger_mode === 'schedule'">
                {{ rule.cron_expression }}（{{ rule.schedule_enabled ? '已启用' : '已停用' }}）<br />
                下次 {{ formatBeijingTime(rule.next_run_at) }}
              </template>
              <template v-else>—</template>
            </td>
            <td>{{ rule.is_active ? '启用' : '停用' }}</td>
            <td>{{ formatBeijingTime(rule.updated_at) }}</td>
            <td v-if="isAdmin">
              <button type="button" @click="startEditing(rule)">编辑</button>
            </td>
          </tr>
          <tr v-if="rules.length === 0">
            <td :colspan="isAdmin ? 12 : 11">暂无采集规则</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
