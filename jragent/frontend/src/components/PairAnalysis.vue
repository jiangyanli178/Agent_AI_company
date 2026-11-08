<template>
  <div class="pair">
    <div class="between wrap">
      <b>组合分析 · {{ data.symbol_code }}</b>
      <span class="tag" :class="verdictClass">{{ data.verdict }}</span>
    </div>

    <table class="table" style="margin-top:10px">
      <thead>
        <tr><th>策略</th><th>收益</th><th>回撤</th><th>夏普</th><th>持仓占比</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>{{ data.a.name }}<span class="tiny"> {{ fmt(data.a.params) }}</span></td>
          <td class="num" :class="cls(data.a.total_return_pct)">{{ pct(data.a.total_return_pct) }}</td>
          <td class="num down">-{{ data.a.max_drawdown_pct }}%</td>
          <td class="num">{{ data.a.sharpe }}</td>
          <td class="num">{{ data.a.exposure_pct }}%</td>
        </tr>
        <tr>
          <td>{{ data.b.name }}<span class="tiny"> {{ fmt(data.b.params) }}</span></td>
          <td class="num" :class="cls(data.b.total_return_pct)">{{ pct(data.b.total_return_pct) }}</td>
          <td class="num down">-{{ data.b.max_drawdown_pct }}%</td>
          <td class="num">{{ data.b.sharpe }}</td>
          <td class="num">{{ data.b.exposure_pct }}%</td>
        </tr>
        <tr class="combo">
          <td><b>等权组合 {{ data.combined.weight }}</b></td>
          <td class="num" :class="cls(data.combined.total_return_pct)"><b>{{ pct(data.combined.total_return_pct) }}</b></td>
          <td class="num down">-{{ data.combined.max_drawdown_pct }}%</td>
          <td class="num">{{ data.combined.sharpe }}</td>
          <td class="num">—</td>
        </tr>
      </tbody>
    </table>

    <div class="facts">
      <div>
        <span class="tiny">收益相关性</span>
        <b class="num" :class="corrClass">{{ data.correlation }}</b>
      </div>
      <div>
        <span class="tiny">持仓冲突</span>
        <b class="num">{{ data.conflict_pct }}%</b>
        <span class="tiny">（{{ data.conflict_days }} 天）</span>
      </div>
      <div>
        <span class="tiny">双方都空仓</span>
        <b class="num">{{ data.both_idle_pct }}%</b>
      </div>
      <div>
        <span class="tiny">买入持有</span>
        <b class="num muted">{{ pct(data.benchmark_return_pct) }}</b>
      </div>
    </div>

    <div class="notice" style="margin-top:10px">{{ data.advice }}</div>
    <div class="tiny" style="margin-top:6px">
      区间 {{ data.period }} · {{ data.combined.note }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })

const pct = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const fmt = (p) => (p ? '(' + Object.values(p).join('/') + ')' : '')

const verdictClass = computed(() => {
  const v = props.data.verdict
  if (v === '互补性强' || v === '互补性较好') return 'tag-primary'
  if (v === '高度重叠') return 'tag-warn'
  return ''
})

const corrClass = computed(() => {
  const c = props.data.correlation
  if (c < 0.3) return 'up'
  if (c >= 0.75) return 'down'
  return ''
})
</script>

<style scoped>
.pair { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--bg-soft); }
.combo { background: rgba(83,74,183,.05); }
.facts {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
  margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);
}
@media (max-width: 560px) { .facts { grid-template-columns: repeat(2, 1fr); } }
.facts > div { display: flex; flex-direction: column; gap: 2px; }
.facts b { font-size: 15px; }
.tag-warn { background: #fdf3e7; color: var(--warn); }
</style>
