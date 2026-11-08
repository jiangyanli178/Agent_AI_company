<template>
  <div>
    <div class="row between wrap" style="margin-bottom:8px">
      <div class="row legend">
        <span v-if="hasMA"><i class="line" style="background:#e8a33d"></i>快线 MA{{ fastLabel }}</span>
        <span v-if="hasMA"><i class="line" style="background:#534ab7"></i>慢线 MA{{ slowLabel }}</span>
        <label class="toggle">
          <input type="checkbox" v-model="showMarkers" />
          <span>买卖点</span>
        </label>
      </div>
      <span class="tiny">滚轮缩放 · 按住拖动 · 默认只看最近半年</span>
    </div>
    <div ref="el" :style="{height: height + 'px', width: '100%'}"></div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  result: { type: Object, required: true },
  height: { type: Number, default: 420 },
})

const el = ref(null)
const showMarkers = ref(true)
let chart = null

const hasMA = computed(() => (props.result.ma_fast || []).some(v => v != null))
const fastLabel = computed(() => props.result.params?.fast ?? '')
const slowLabel = computed(() => props.result.params?.slow ?? '')

// 默认只展示最近约 120 个交易日，K 线才看得清；
// 想看更早就用滚轮或拖下面滑块。
function zoomStart(total) {
  const n = 120
  return total <= n ? 0 : 100 - (n / total) * 100
}

function build() {
  const r = props.result
  if (!el.value || !r?.ohlc?.length) return
  if (!chart) {
    chart = echarts.init(el.value)
    // 与下面两张净值图联动：上面拖动，下面跟着动
    chart.group = 'bt-charts'
    echarts.connect('bt-charts')
  }

  const dates = r.dates
  const total = dates.length
  const series = [
    {
      name: 'K线', type: 'candlestick', data: r.ohlc,
      itemStyle: {
        color: '#c0362c', color0: '#1a7f4b',
        borderColor: '#c0362c', borderColor0: '#1a7f4b',
      },
      barMaxWidth: 8,
    },
  ]

  if (hasMA.value) {
    series.push({
      name: `快线 MA${fastLabel.value}`, type: 'line', data: r.ma_fast,
      showSymbol: false, smooth: true, connectNulls: true, z: 5,
      lineStyle: { width: 2.2, color: '#e8a33d' }, itemStyle: { color: '#e8a33d' },
    })
    series.push({
      name: `慢线 MA${slowLabel.value}`, type: 'line', data: r.ma_slow,
      showSymbol: false, smooth: true, connectNulls: true, z: 5,
      lineStyle: { width: 2.2, color: '#534ab7' }, itemStyle: { color: '#534ab7' },
    })
  }

  // 买卖点只画三角、不写"买/卖"文字，否则一眼全是字
  if (showMarkers.value) {
    const toPoints = (type) => (r.markers || [])
      .filter(m => m.type === type)
      .map(m => {
        const i = dates.indexOf(m.date)
        return i >= 0 ? [i, m.price] : null
      })
      .filter(Boolean)

    const buys = toPoints('buy')
    const sells = toPoints('sell')
    if (buys.length) {
      series.push({
        name: '买入', type: 'scatter', data: buys,
        symbol: 'triangle', symbolSize: 9,
        itemStyle: { color: '#c0362c', borderColor: '#fff', borderWidth: 1 }, z: 12,
      })
    }
    if (sells.length) {
      series.push({
        name: '卖出', type: 'scatter', data: sells,
        symbol: 'triangle', symbolRotate: 180, symbolSize: 9,
        itemStyle: { color: '#1a7f4b', borderColor: '#fff', borderWidth: 1 }, z: 12,
      })
    }
  }

  chart.setOption({
    animation: false,
    grid: { left: 54, right: 16, top: 12, bottom: 58 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter(params) {
        const k = params.find(p => p.seriesType === 'candlestick')
        if (!k) return ''
        const [, o, c, l, h] = k.data
        const rows = params
          .filter(p => p.seriesType === 'line' && p.value != null)
          .map(p => `${p.seriesName}：<b>${Number(p.value).toFixed(2)}</b>`)
        return [`<b>${k.axisValue}</b>`, `开 ${o}　收 ${c}`, `低 ${l}　高 ${h}`, ...rows]
          .join('<br>')
      },
    },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, color: '#9a9a90' } },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: '#f2f2ec' } },
      axisLabel: { fontSize: 10 },
    },
    dataZoom: [
      {
        type: 'inside',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        start: zoomStart(total), end: 100,
      },
      {
        type: 'slider', height: 16, bottom: 10,
        borderColor: 'transparent', backgroundColor: '#f7f7f5',
        fillerColor: 'rgba(83,74,183,0.10)',
        handleStyle: { color: '#534ab7' },
        textStyle: { fontSize: 9 },
      },
    ],
    series,
  }, true)
}

function resize() { chart && chart.resize() }

onMounted(() => {
  build()
  window.addEventListener('resize', resize)
})
watch(() => props.result, build, { deep: true })
watch(showMarkers, build)
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart && chart.dispose()
})
</script>

<style scoped>
.legend { gap: 16px; font-size: 12px; color: var(--text-2); flex-wrap: wrap; }
.legend > span { display: inline-flex; align-items: center; gap: 5px; }
.line { display: inline-block; width: 16px; height: 2px; border-radius: 2px; }
.toggle { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.toggle input { accent-color: var(--primary); }
</style>
