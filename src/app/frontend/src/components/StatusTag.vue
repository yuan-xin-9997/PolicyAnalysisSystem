<script setup lang="ts">
import type { TaskStatus } from '../api/types'

const props = defineProps<{ status: TaskStatus }>()

const statusMap: Record<TaskStatus, { label: string; tone: string }> = {
  pending: { label: '等待', tone: 'muted' },
  running: { label: '运行', tone: 'running' },
  succeeded: { label: '成功', tone: 'success' },
  partially_succeeded: { label: '部分成功', tone: 'warning' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'muted' },
}
</script>

<template>
  <span class="status-pill" :class="`status-${statusMap[props.status].tone}`">
    {{ statusMap[props.status].label }}
  </span>
</template>
