<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed } from 'vue'
import type { WordRelationResult } from '../../api/types'
import BaseChart from './BaseChart.vue'

const props = defineProps<{ result: WordRelationResult }>()

const option = computed<EChartsOption>(
  () =>
    ({
      tooltip: {},
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          label: { show: true },
          force: { repulsion: 220, edgeLength: 110 },
          data: props.result.nodes.map((name) => ({ name, symbolSize: 30 })),
          links: props.result.items.map((item) => ({
            source: item.word1,
            target: item.word2,
            value: item.co_count,
          })),
        },
      ],
    }) as EChartsOption,
)
</script>

<template>
  <BaseChart :option="option" />
</template>
