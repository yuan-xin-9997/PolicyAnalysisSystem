<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import { apiRequest } from '../../api/client'
import type { CollectionRule, PolicyCategory, SourceSummary } from '../../api/types'

const props = defineProps<{
  sources: SourceSummary[]
  categories: PolicyCategory[]
  rule?: CollectionRule | null
}>()

const emit = defineEmits<{ saved: [rule: CollectionRule]; cancelled: [] }>()

const isEditing = computed(() => Boolean(props.rule))

const form = reactive({
  name: '',
  sourceCode: '',
  categoryCode: '',
  includeKeywords: '',
  excludeKeywords: '',
  rssUrls: '',
  channelUrls: '',
  historyYears: 5,
  isActive: true,
  triggerMode: 'manual',
  cronExpression: '',
  scheduleEnabled: false,
})
const saving = ref(false)
const errorMessage = ref('')
const validationMessage = ref('')

const canSave = computed(() => props.sources.length > 0 && props.categories.length > 0)

watch(
  () => props.rule,
  (rule) => {
    validationMessage.value = ''
    errorMessage.value = ''
    if (rule) {
      form.name = rule.name
      form.sourceCode = rule.source.code
      form.categoryCode = rule.category.code
      form.includeKeywords = rule.include_keywords.join('\n')
      form.excludeKeywords = rule.exclude_keywords.join('\n')
      form.rssUrls = rule.discovery.rss_urls.join('\n')
      form.channelUrls = rule.discovery.channel_urls.join('\n')
      form.historyYears = rule.history_years
      form.isActive = rule.is_active
      form.triggerMode = rule.trigger_mode
      form.cronExpression = rule.cron_expression ?? ''
      form.scheduleEnabled = rule.schedule_enabled
    } else {
      form.name = ''
      form.sourceCode = ''
      form.categoryCode = ''
      form.includeKeywords = ''
      form.excludeKeywords = ''
      form.rssUrls = ''
      form.channelUrls = ''
      form.historyYears = 5
      form.isActive = true
      form.triggerMode = 'manual'
      form.cronExpression = ''
      form.scheduleEnabled = false
    }
  },
  { immediate: true },
)

function normalizeList(value: string): string[] {
  return [...new Set(value.split(/[,\n，]/).map((item) => item.trim()).filter(Boolean))]
}

function isFiveFieldCron(value: string): boolean {
  return value.trim().split(/\s+/).length === 5
}

async function saveRule(): Promise<void> {
  validationMessage.value = ''
  errorMessage.value = ''
  const includeKeywords = normalizeList(form.includeKeywords)
  const excludeKeywords = normalizeList(form.excludeKeywords)
  const rssUrls = normalizeList(form.rssUrls)
  const channelUrls = normalizeList(form.channelUrls)

  if (!form.name.trim() || !form.sourceCode || !form.categoryCode || includeKeywords.length === 0) {
    validationMessage.value = '请填写名称、来源、类别和至少一个包含词。'
    return
  }
  if (form.historyYears < 1 || form.historyYears > 20) {
    validationMessage.value = '历史窗口必须在 1 到 20 年之间。'
    return
  }
  if (rssUrls.length + channelUrls.length === 0) {
    validationMessage.value = '请至少配置一个 RSS 或栏目入口。'
    return
  }
  const cronExpression = form.cronExpression.trim()
  if (form.triggerMode === 'schedule' && !isFiveFieldCron(cronExpression)) {
    validationMessage.value = '定时运行的 Cron 必须是北京时间 5 段表达式。'
    return
  }
  const enablingSchedule = form.triggerMode === 'schedule' && form.scheduleEnabled && !props.rule?.schedule_enabled
  if (enablingSchedule && !window.confirm('启用定时运行前请确认已完成 WebFetch 检查和手工回填。是否继续？')) {
    return
  }

  const body: Record<string, unknown> = {
    name: form.name.trim(),
    source_code: form.sourceCode,
    category_code: form.categoryCode,
    include_keywords: includeKeywords,
    exclude_keywords: excludeKeywords,
    history_years: form.historyYears,
    discovery: { rss_urls: rssUrls, channel_urls: channelUrls },
    is_active: form.isActive,
  }
  if (form.triggerMode === 'schedule') {
    body.trigger_mode = 'schedule'
    body.cron_expression = cronExpression
    body.schedule_enabled = form.scheduleEnabled
  } else {
    body.trigger_mode = 'manual'
  }

  saving.value = true
  try {
    const rule = props.rule
      ? await apiRequest<CollectionRule>(`/collection-rules/${props.rule.id}`, {
          method: 'PATCH',
          body: JSON.stringify(body),
        })
      : await apiRequest<CollectionRule>('/collection-rules', {
          method: 'POST',
          body: JSON.stringify(body),
        })
    emit('saved', rule)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则保存失败。'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="form-card" :aria-label="isEditing ? '编辑采集规则' : '新增采集规则'" @submit.prevent="saveRule">
    <h2>{{ isEditing ? `编辑采集规则 #${props.rule?.id}` : '新增采集规则' }}</h2>
    <p v-if="validationMessage" role="alert" class="error-state">{{ validationMessage }}</p>
    <p v-if="errorMessage" role="alert" class="error-state">{{ errorMessage }}</p>

    <label>
      规则名称
      <input v-model="form.name" name="name" />
    </label>
    <label>
      来源
      <select v-model="form.sourceCode" name="source_code">
        <option value="">请选择</option>
        <option v-for="source in sources" :key="source.code" :value="source.code">{{ source.name }}</option>
      </select>
    </label>
    <label>
      类别
      <select v-model="form.categoryCode" name="category_code">
        <option value="">请选择</option>
        <option v-for="category in categories" :key="category.code" :value="category.code">{{ category.name }}</option>
      </select>
    </label>
    <label>
      包含词
      <textarea v-model="form.includeKeywords" name="include_keywords" placeholder="用逗号或换行分隔"></textarea>
    </label>
    <label>
      排除词
      <textarea v-model="form.excludeKeywords" name="exclude_keywords" placeholder="用逗号或换行分隔"></textarea>
    </label>
    <label>
      RSS URL
      <textarea v-model="form.rssUrls" name="rss_urls" placeholder="用逗号或换行分隔"></textarea>
    </label>
    <label>
      栏目 URL
      <textarea v-model="form.channelUrls" name="channel_urls" placeholder="用逗号或换行分隔"></textarea>
    </label>
    <label>
      历史窗口
      <input v-model.number="form.historyYears" name="history_years" type="number" min="1" max="20" />
    </label>
    <label class="checkbox-line">
      <input v-model="form.isActive" name="is_active" type="checkbox" />
      启用规则
    </label>

    <fieldset class="trigger-mode-group">
      <legend>触发方式</legend>
      <label class="checkbox-line">
        <input v-model="form.triggerMode" name="trigger_mode" type="radio" value="manual" />
        手工触发（仅在任务中心手动触发采集）
      </label>
      <label class="checkbox-line">
        <input v-model="form.triggerMode" name="trigger_mode" type="radio" value="schedule" />
        定时运行（按 Cron 自动执行，也可手工触发）
      </label>
      <template v-if="form.triggerMode === 'schedule'">
        <label>
          Cron 表达式（北京时间 5 段，例如 0 9 * * *）
          <input v-model="form.cronExpression" name="cron_expression" placeholder="0 9 * * *" />
        </label>
        <label class="checkbox-line">
          <input v-model="form.scheduleEnabled" name="schedule_enabled" type="checkbox" />
          启用定时运行
        </label>
      </template>
    </fieldset>

    <div class="filter-actions">
      <button type="submit" :disabled="saving || !canSave">{{ saving ? '保存中…' : '保存规则' }}</button>
      <button v-if="isEditing" type="button" @click="emit('cancelled')">取消编辑</button>
    </div>
  </form>
</template>
