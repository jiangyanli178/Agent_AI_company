<template>
  <div class="wrap">
    <div class="between" style="margin-bottom:6px">
      <div class="row legend">
        <span><i class="line" style="background:#534ab7"></i>{{ label }}净值</span>
        <span><i class="line dashed"></i>买入持有</span>
        <span><i class="line" style="background:#c0362c"></i>回撤</span>
      </div>
      <span class="tiny">{{ mode === 'compound' ? '赚的钱滚进本金继续投' : '每笔收益直接累加，不复利' }}</span>
    </div>
    <div ref="el" :style="{height: height + 'px', width: '100%'}"></div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  result: { type: Object, required: true },
  mode: { type: String, default: 'compound' },   // compound | simple
  height: { type: Number, default: 300 },
})

const el = ref(null)
let chart = null

const label = props.mode === 'compound' ? '复利' : '单利'

// 与 K 线图共用「只看最近 120 个交易日」的默认窗口
function zoomStart(total) {
  const n = 120
  return total <= n ? 0 : 100 - (n / total) * 100
}

function seriesData() {
  const r = props.result
  if (props.mode === 'compound') {
    return { nav: r.equity, dd: r.drawdown_curve, bench: r.benchmark_curve }
  }
  // 单利图的回撤副图必须用「峰值口径」（从单利净值自己的高点回落）。
  // 用 simple_drawdown_curve（本金口径）的话，单利净值多数时间在 1.0 以上，
  // 那些天算出来全是 0，副图会是一条贴底的假线。
  return {
    nav: r.simple_curve,
    dd: r.simple_peak_drawdown_curve || r.simple_drawdown_curve,
    bench: r.benchmark_curve,
  }
}

function render() {
  const r = props.result
  if (!el.value || !r?.dates) return
  if (!chart) {
    chart = echarts.init(el.value)
    // 与 K 线图同组，实现上下联动缩放
    chart.group = 'bt-charts'
    echarts.connect('bt-charts')
  }
  const { nav, dd, bench } = seriesData()

  chart.setOption({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 52, right: 16, top: 22, height: '52%' },
      { left: 52, right: 16, top: '76%', height: '16%' },
    ],
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(3)),
    },
    xAxis: [
      {
        type: 'category', data: r.dates, gridIndex: 0,
        axisLabel: { show: false }, axisTick: { show: false },
      },
      {
        type: 'category', data: r.dates, gridIndex: 1,
        axisLabel: { fontSize: 10, color: '#9a9a90' },
      },
    ],
    yAxis: [
      {
        type: 'value', gridIndex: 0, scale: true,
        splitLine: { lineStyle: { color: '#f2f2ec' } },
        axisLabel: { fontSize: 10 },
      },
      {
        type: 'value', gridIndex: 1, max: 1, inverse: true,
        axisLabel: { formatter: (v) => (v * 100).toFixed(0) + '%', fontSize: 9 },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      {
        type: 'inside', xAxisIndex: [0, 1],
        zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false,
        // 与 K 线图保持同样的默认窗口，联动时才不会一上一下错位
        start: zoomStart(r.dates.length), end: 100,
      },
    ],
    series: [
      {
        name: `${label}净值`, type: 'line', data: nav,
        xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 2, color: '#534ab7' }, itemStyle: { color: '#534ab7' },
        z: 4,
      },
      {
        name: '买入持有', type: 'line', data: bench,
        xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 1.3, type: 'dashed', color: '#b8b8ae' },
        itemStyle: { color: '#b8b8ae' },
        z: 3,
      },
      {
        name: '回撤', type: 'line', data: dd,
        xAxisIndex: 1, yAxisIndex: 1,
        showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 1, color: '#c0362c' }, itemStyle: { color: '#c0362c' },
        areaStyle: { color: 'rgba(192,54,44,0.13)' },
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

<style scoped>
.legend { gap: 14px; font-size: 12px; color: var(--text-2); flex-wrap: wrap; }
.legend > span { display: inline-flex; align-items: center; gap: 5px; }
.line { display: inline-block; width: 15px; height: 2px; border-radius: 2px; }
.dashed { height: 0; border-top: 2px dashed #b8b8ae; }
</style>
