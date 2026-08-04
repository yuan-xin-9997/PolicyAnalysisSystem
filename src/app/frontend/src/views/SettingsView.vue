<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'

interface SettingsResponse {
  values: Record<string, unknown>
  sources: Record<string, 'default' | 'config_file' | 'environment'>
  webfetch: { status: 'ready' | 'unavailable' | 'configured' | 'not_configured'; checked: boolean }
}

interface SettingRow {
  key: string
  value: string
  source: string
}

const response = ref<SettingsResponse | null>(null)
const loading = ref(true)
const errorMessage = ref('')
const rows = computed<SettingRow[]>(() =>
  response.value ? flattenValues(response.value.values, response.value.sources) : [],
)
const SENSITIVE_KEY = /(password|secret|token|api_key)/i

onMounted(loadSettings)

async function loadSettings(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    response.value = await apiRequest<SettingsResponse>('/settings/effective')
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '系统配置加载失败。'
  } finally {
    loading.value = false
  }
}

function flattenValues(
  value: unknown,
  sources: SettingsResponse['sources'],
  prefix = '',
  maskedAncestor = false,
): SettingRow[] {
  if (Array.isArray(value)) {
    if (value.length === 0) return [settingRow(prefix, '[]', sources)]
    return value.flatMap((child, index) =>
      flattenValues(child, sources, `${prefix}[${index}]`, maskedAncestor),
    )
  }
  if (!isRecord(value)) {
    return [settingRow(prefix, maskedAncestor ? '********' : formatValue(value), sources)]
  }
  const entries = Object.entries(value)
  if (entries.length === 0) return [settingRow(prefix, '{}', sources)]
  return entries.flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return flattenValues(child, sources, path, maskedAncestor || SENSITIVE_KEY.test(key))
  })
}

function settingRow(
  key: string,
  value: string,
  sources: SettingsResponse['sources'],
): SettingRow {
  return { key, value, source: sourceLabel(sources[key]) }
}

function sourceLabel(source: SettingsResponse['sources'][string] | undefined): string {
  return { default: '默认值', config_file: '配置文件', environment: '环境变量' }[source || 'default']
}

function webfetchLabel(status: SettingsResponse['webfetch']['status']): string {
  return {
    ready: '可用',
    unavailable: '不可用',
    configured: '已配置',
    not_configured: '未配置',
  }[status]
}

function formatValue(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'string') return value || '（空）'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
</script>

<template>
  <section class="page-panel" aria-labelledby="settings-title">
    <header class="page-heading">
      <div>
        <span class="eyebrow">RUNTIME CONFIGURATION</span>
        <h1 id="settings-title">系统配置</h1>
        <p>只读展示配置文件、环境变量覆盖和默认值共同形成的生效配置。</p>
      </div>
      <span v-if="response" class="status-pill" :class="{ muted: !['ready', 'configured'].includes(response.webfetch.status) }">
        WebFetch {{ webfetchLabel(response.webfetch.status) }}
      </span>
    </header>

    <p v-if="loading" role="status" class="state-card">正在加载系统配置…</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-else-if="rows.length === 0" class="state-card">暂无配置</p>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">配置项</th>
            <th scope="col">生效值</th>
            <th scope="col">来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key">
            <td><code>{{ row.key }}</code></td>
            <td class="setting-value">{{ row.value }}</td>
            <td>{{ row.source }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
