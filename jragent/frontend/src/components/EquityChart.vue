<template>
  <div ref="el" :style="{height: height + 'px', width: '100%'}"></div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  result: { type: Object, required: true },
  height: { type: Number, default: 320 },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value || !props.result?.dates) return
  const r = props.result
  if (!chart) chart = echarts.init(el.value)

  chart.setOption({
    animation: false,
    grid: { left: 52, right: 18, top: 34, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(3)),
    },
    legend: { data: ['本策略', '买入持有'], top: 4, textStyle: { fontSize: 12 } },
    xAxis: {
      type: 'category', data: r.dates,
      axisLabel: { fontSize: 10, color: '#9a9a90' },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value', scale: true,
      splitLine: { lineStyle: { color: '#f2f2ec' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: '本策略', type: 'line', data: r.equity,
        showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 2, color: '#534ab7' }, itemStyle: { color: '#534ab7' },
        areaStyle: { color: 'rgba(83,74,183,0.05)' },
        z: 4,
      },
      {
        name: '买入持有', type: 'line', data: r.benchmark_curve || [],
        showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 1.4, type: 'dashed', color: '#9a9a90' },
        itemStyle: { color: '#9a9a90' },
        z: 3,
      },
    ],
  }, true)
}

function resize() { chart && chart.resize() }

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
watch(() => props.result, render, { deep: true })
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart && chart.dispose()
})
</script>
