<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, apiRequest } from '../../api/client'
import type { PolicyDetail } from '../../api/types'
import { formatBeijingTime } from '../../utils/time'

const props = defineProps<{ policyId: number | string }>()
const loading = ref(true)
const errorMessage = ref('')
const policy = ref<PolicyDetail | null>(null)

onMounted(() => {
  void loadPolicy()
})

async function loadPolicy(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    policy.value = await apiRequest<PolicyDetail>(`/policies/${props.policyId}`)
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '政策详情加载失败。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="page-panel" aria-labelledby="policy-detail-title">
    <p v-if="loading" role="status" class="state-card">正在加载政策详情</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <article v-else-if="policy" class="policy-detail">
      <span class="eyebrow">{{ policy.source.name }} · {{ policy.category.name }}</span>
      <h1 id="policy-detail-title">{{ policy.title }}</h1>
      <dl class="metadata-grid">
        <div>
          <dt>发布部门</dt>
          <dd>{{ policy.publisher }}</dd>
        </div>
        <div>
          <dt>发布时间</dt>
          <dd>{{ formatBeijingTime(policy.published_at) }}</dd>
        </div>
        <div>
          <dt>最近抓取时间</dt>
          <dd>{{ formatBeijingTime(policy.last_crawled_at) }}</dd>
        </div>
        <div>
          <dt>原文链接</dt>
          <dd>
            <a :href="policy.canonical_url" target="_blank" rel="noopener noreferrer">打开原文</a>
          </dd>
        </div>
      </dl>
      <pre class="policy-content">{{ policy.content_text }}</pre>
    </article>
  </section>
</template>
