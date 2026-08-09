<script setup lang="ts">
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ option: EChartsOption }>()

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function render(): void {
  if (chart) {
    chart.setOption(props.option, { notMerge: true })
  }
}

function handleResize(): void {
  chart?.resize()
}

onMounted(() => {
  if (container.value) {
    chart = echarts.init(container.value)
    render()
    window.addEventListener('resize', handleResize)
  }
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="container" class="chart-container"></div>
</template>
