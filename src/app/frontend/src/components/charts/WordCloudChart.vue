<script setup lang="ts">
import 'echarts-wordcloud'
import type { EChartsOption } from 'echarts'
import { computed } from 'vue'
import type { WordFrequencyItem } from '../../api/types'
import BaseChart from './BaseChart.vue'

const props = defineProps<{ items: WordFrequencyItem[] }>()

const option = computed<EChartsOption>(
  () =>
    ({
      tooltip: { show: true },
      series: [
        {
          type: 'wordCloud',
          shape: 'circle',
          sizeRange: [12, 60],
          rotationRange: [-30, 30],
          gridSize: 8,
          textStyle: { fontFamily: 'sans-serif', fontWeight: 'bold' },
          data: props.items.map((item) => ({ name: item.word, value: item.frequency })),
        },
      ],
    }) as EChartsOption,
)
</script>

<template>
  <BaseChart :option="option" />
</template>
