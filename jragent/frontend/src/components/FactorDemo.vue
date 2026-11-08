<template>
  <div v-if="visible" class="mask" @click.self="close">
    <div class="panel">
      <div class="between" style="margin-bottom:4px">
        <b>{{ factor.name }}</b>
        <button class="tiny" @click="close">关闭</button>
      </div>
      <div class="tiny" style="margin-bottom:10px">
        下面是用真实行情算出来的，不是示意图
      </div>

      <p class="muted" style="font-size:13px;margin-bottom:12px">{{ factor.summary }}</p>

      <div v-if="loading" class="loading">正在算…</div>
      <div v-else-if="error" class="notice">{{ error }}</div>
      <template v-else>
        <div ref="el" style="height:300px;width:100%"></div>
        <div class="notice" style="margin-top:10px">{{ demo.note }}</div>

        <div class="detail-grid">
          <div>
            <div class="tiny">怎么算</div>
            <p>{{ factor.formula }}</p>
          </div>
          <div>
            <div class="tiny">怎么用</div>
            <p>{{ factor.usage_note }}</p>
          </div>
          <div v-if="factor.params_hint && factor.params_hint !== '—'">
            <div class="tiny">常见参数</div>
            <p>{{ factor.params_hint }}</p>
          </div>
        </div>

        <div class="row wrap" style="margin-top:12px">
          <a class="btn btn-plain btn-sm"
             :href="`/api/factors/${factor.code}/export?symbol=${demo.symbol || '600519'}`"
             :download="`${factor.code}_${demo.symbol || ''}.csv`">
            下载逐日数据 CSV
          </a>
          <span class="tiny">自己用 Excel 核对一遍也行，算法口径都写在「怎么算」里</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import api from '../api'

const props = defineProps({
  modelValue: Boolean,
  factor: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const el = ref(null)
const demo = ref(null)
const loading = ref(false)
const error = ref('')
let chart = null

async function load() {
  if (!props.factor) return
  loading.value = true
  error.value = ''
  demo.value = null

  // 弹窗关了再开时，旧的 canvas 已经被 v-if 销毁，
  // 但 chart 实例还指着一个不在文档里的节点 —— 必须扔掉重建
  if (chart) { chart.dispose(); chart = null }

  try {
    demo.value = await api.get(`/factors/${props.factor.code}/demo`)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }

  if (demo.value) {
    // 关键顺序：先让 loading 变 false，容器才会从 v-if 里出来；
    // 等 DOM 挂上再 init。之前是先 render 后关 loading，
    // 那时 el.value 还是 null，图表永远画不出来 —— 弹窗一片空白就是这个原因。
    await nextTick()
    render()
  }
}

function render() {
  if (!el.value || !demo.value) return
  if (!chart) chart = echarts.init(el.value)
  const d = demo.value

  const series = d.series.map(s => {
    const base = {
      name: s.name,
      data: s.data,
      showSymbol: false,
      connectNulls: true,
      itemStyle: { color: s.color },
      lineStyle: { width: 1.8, color: s.color },
      z: s.color === '#e3e3da' || s.color === '#c9c9c0' ? 1 : 3,
    }
    if (s.kind === 'bar') {
      return { ...base, type: 'bar', barMaxWidth: 6,
               itemStyle: { color: s.color, opacity: .55 } }
    }
    if (s.kind === 'area') {
      return { ...base, type: 'line', areaStyle: { color: s.color, opacity: .15 } }
    }
    return { ...base, type: 'line', smooth: false }
  })

  // 水平参考线（超买超卖、阈值）
  if (d.bands?.length) {
    const main = series[0]
    if (main) {
      main.markLine = {
        silent: true, symbol: 'none',
        data: d.bands.map(b => ({
          yAxis: b.value,
          lineStyle: { color: b.color, type: 'dashed', width: 1 },
          label: { formatter: b.label, fontSize: 10, color: b.color, position: 'insideEndTop' },
        })),
      }
    }
  }

  // 交叉点标记
  if (d.marks?.length && series.length) {
    const idx = Object.fromEntries(d.dates.map((x, i) => [x, i]))
    series[0].markPoint = {
      symbol: 'circle', symbolSize: 7,
      itemStyle: { color: d.target === 'up' ? '#c0362c' : '#1a7f4b' },
      data: d.marks.map(m => ({
        coord: [idx[m.date], m.price],
        value: m.type === 'up' ? '上穿' : '下穿',
      })),
      label: { show: false },
    }
  }

  chart.setOption({
    animation: false,
    grid: { left: 52, right: 18, top: 28, bottom: 28 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    xAxis: {
      type: 'category', data: d.dates,
      axisLabel: { fontSize: 9, color: '#9a9a90' },
    },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: '#f2f2ec' } },
      axisLabel: { fontSize: 9 },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 6,
      borderColor: 'transparent', fillerColor: 'rgba(83,74,183,.1)' }],
    series,
  }, true)
}

watch(() => props.modelValue, (v) => {
  if (v) load()
})
watch(() => props.factor, () => {
  if (props.modelValue) load()
})

function close() { visible.value = false }

function onResize() { if (chart) chart.resize() }
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.42);
  display: flex; align-items: center; justify-content: center; z-index: 200; padding: 16px;
}
.panel {
  background: #fff; border-radius: 14px; padding: 20px;
  width: 720px; max-width: 100%; max-height: 90vh; overflow-y: auto;
}
.detail-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px;
  margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border);
}
@media (max-width: 640px) { .detail-grid { grid-template-columns: 1fr; } }
.detail-grid p { font-size: 12.5px; line-height: 1.7; margin-top: 4px; }
</style>
