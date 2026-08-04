<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { apiRequest } from '../../api/client'
import type { CollectionRule, PolicyCategory, SourceSummary } from '../../api/types'

const props = defineProps<{
  sources: SourceSummary[]
  categories: PolicyCategory[]
}>()

const emit = defineEmits<{ saved: [rule: CollectionRule] }>()

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
})
const saving = ref(false)
const errorMessage = ref('')
const validationMessage = ref('')

const canSave = computed(() => props.sources.length > 0 && props.categories.length > 0)

function normalizeList(value: string): string[] {
  return [...new Set(value.split(/[,\n，]/).map((item) => item.trim()).filter(Boolean))]
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

  saving.value = true
  try {
    const rule = await apiRequest<CollectionRule>('/collection-rules', {
      method: 'POST',
      body: JSON.stringify({
        name: form.name.trim(),
        source_code: form.sourceCode,
        category_code: form.categoryCode,
        include_keywords: includeKeywords,
        exclude_keywords: excludeKeywords,
        history_years: form.historyYears,
        discovery: { rss_urls: rssUrls, channel_urls: channelUrls },
        is_active: form.isActive,
      }),
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
  <form class="form-card" aria-label="新增采集规则" @submit.prevent="saveRule">
    <h2>新增采集规则</h2>
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
    <button type="submit" :disabled="saving || !canSave">{{ saving ? '保存中…' : '保存规则' }}</button>
  </form>
</template>
