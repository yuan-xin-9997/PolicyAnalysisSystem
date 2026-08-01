<script setup lang="ts">
import { computed } from 'vue'

type PlaceholderPage = 'policies' | 'tasks' | 'push' | 'analysis'

const props = defineProps<{ page: PlaceholderPage }>()

const content = computed(() => ({
  policies: {
    eyebrow: 'POLICY DATABASE',
    title: '政策数据库',
    description: '政策筛选、检索和详情模块将在后续业务任务中接入。',
  },
  tasks: {
    eyebrow: 'TASK CENTER',
    title: '任务中心',
    description: '采集规则、定时计划和任务日志模块将在后续业务任务中接入。',
  },
  push: {
    eyebrow: 'DELIVERY ROADMAP',
    title: '推送管理',
    description: '后续将规划按政策类别和发布部门进行邮件推送。',
  },
  analysis: {
    eyebrow: 'ANALYSIS ROADMAP',
    title: '政策分析',
    description: '后续将规划词频、表述差异与大模型辅助研判能力。',
  },
})[props.page])
</script>

<template>
  <section class="page-panel placeholder-panel" :aria-labelledby="`${page}-title`">
    <span class="eyebrow">{{ content.eyebrow }}</span>
    <h1 :id="`${page}-title`">{{ content.title }}</h1>
    <p>{{ content.description }}</p>
    <span class="planning-badge">功能规划中</span>
  </section>
</template>
