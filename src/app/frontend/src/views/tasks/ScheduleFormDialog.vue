<script setup lang="ts">
import { reactive, ref } from 'vue'

import { apiRequest } from '../../api/client'
import type { CollectionRule, Schedule } from '../../api/types'

defineProps<{ rules: CollectionRule[] }>()
const emit = defineEmits<{ saved: [schedule: Schedule] }>()

const form = reactive({ ruleId: '', cronExpression: '', isActive: false })
const saving = ref(false)
const validationMessage = ref('')
const errorMessage = ref('')

function isFiveFieldCron(value: string): boolean {
  return value.trim().split(/\s+/).length === 5
}

async function saveSchedule(): Promise<void> {
  validationMessage.value = ''
  errorMessage.value = ''
  if (!form.ruleId || !isFiveFieldCron(form.cronExpression)) {
    validationMessage.value = 'Cron 必须是北京时间 5 段表达式。'
    return
  }
  if (form.isActive && !window.confirm('启用计划前请确认已完成 WebFetch 检查和手工回填。是否继续？')) {
    return
  }

  saving.value = true
  try {
    const schedule = await apiRequest<Schedule>('/schedules', {
      method: 'POST',
      body: JSON.stringify({ rule_id: Number(form.ruleId), cron_expression: form.cronExpression.trim() }),
    })
    if (form.isActive && !schedule.is_active) {
      const activated = await apiRequest<Schedule>(`/schedules/${schedule.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: true }),
      })
      emit('saved', activated)
      return
    }
    emit('saved', schedule)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计划保存失败。'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form class="form-card" aria-label="新增定时计划" @submit.prevent="saveSchedule">
    <h2>新增定时计划</h2>
    <p>Cron 使用北京时间 5 段表达式，例如：0 9 * * *</p>
    <p v-if="validationMessage" role="alert" class="error-state">{{ validationMessage }}</p>
    <p v-if="errorMessage" role="alert" class="error-state">{{ errorMessage }}</p>

    <label>
      规则
      <select v-model="form.ruleId" name="rule_id">
        <option value="">请选择</option>
        <option v-for="rule in rules" :key="rule.id" :value="String(rule.id)">{{ rule.name }}</option>
      </select>
    </label>
    <label>
      Cron 表达式
      <input v-model="form.cronExpression" name="cron_expression" placeholder="0 9 * * *" />
    </label>
    <label class="checkbox-line">
      <input v-model="form.isActive" name="is_active" type="checkbox" />
      创建后立即启用
    </label>
    <button type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存计划' }}</button>
  </form>
</template>
