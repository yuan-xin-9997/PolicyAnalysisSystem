<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, apiRequest } from '../../api/client'
import type { CollectionRule, Schedule } from '../../api/types'
import { useAuthStore } from '../../stores/auth'
import { formatBeijingTime } from '../../utils/time'
import ScheduleFormDialog from './ScheduleFormDialog.vue'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const loading = ref(true)
const errorMessage = ref('')
const schedules = ref<Schedule[]>([])
const rules = ref<CollectionRule[]>([])

onMounted(() => {
  void loadAll()
})

async function loadAll(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [scheduleItems, ruleItems] = await Promise.all([
      apiRequest<Schedule[]>('/schedules'),
      apiRequest<CollectionRule[]>('/collection-rules'),
    ])
    schedules.value = scheduleItems
    rules.value = ruleItems
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '定时计划加载失败。'
  } finally {
    loading.value = false
  }
}

function upsertSchedule(schedule: Schedule): void {
  const index = schedules.value.findIndex((item) => item.id === schedule.id)
  if (index >= 0) schedules.value[index] = schedule
  else schedules.value = [schedule, ...schedules.value]
}
</script>

<template>
  <section class="page-panel" aria-labelledby="schedules-title">
    <div class="page-heading">
      <div>
        <span class="eyebrow">SCHEDULES</span>
        <h1 id="schedules-title">定时计划</h1>
      </div>
      <RouterLink to="/tasks">返回任务中心</RouterLink>
    </div>

    <p v-if="loading" role="status" class="state-card">正在加载定时计划</p>
    <p v-else-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <template v-else>
      <ScheduleFormDialog v-if="isAdmin" :rules="rules" @saved="upsertSchedule" />
      <p v-else class="state-card">普通用户仅可查看计划，不能新增、启停或编辑。</p>

      <table class="data-table">
        <thead>
          <tr>
            <th>规则</th>
            <th>Cron</th>
            <th>时区</th>
            <th>状态</th>
            <th>下次执行</th>
            <th>上次执行</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="schedule in schedules" :key="schedule.id">
            <td>{{ schedule.rule_name }}</td>
            <td>{{ schedule.cron_expression }}</td>
            <td>{{ schedule.timezone }}</td>
            <td>{{ schedule.is_active ? '启用' : '停用' }}</td>
            <td>{{ formatBeijingTime(schedule.next_run_at) }}</td>
            <td>{{ formatBeijingTime(schedule.last_run_at) }}</td>
          </tr>
          <tr v-if="schedules.length === 0">
            <td colspan="6">暂无定时计划</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
