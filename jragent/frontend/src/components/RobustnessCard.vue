<template>
  <div class="rob">
    <div class="between wrap">
      <b>稳健性检查 · {{ data.strategy }} × {{ data.symbol_code }}</b>
      <span class="tag" :class="riskClass">过拟合风险 {{ data.risk }}</span>
    </div>

    <div class="notice" style="margin-top:10px">{{ data.summary }}</div>

    <div class="split">
      <div class="half">
        <div class="tiny">前半段（样本内）</div>
        <div class="tiny muted">{{ data.split.in_sample.period }}</div>
        <div class="num big" :class="cls(data.split.in_sample.total_return_pct)">
          {{ pct(data.split.in_sample.total_return_pct) }}
        </div>
        <div class="tiny">回撤 {{ data.split.in_sample.max_drawdown_pct }}% · 交易 {{ data.split.in_sample.trade_count }} 次</div>
      </div>
      <div class="half">
        <div class="tiny">后半段（样本外验证）</div>
        <div class="tiny muted">{{ data.split.out_sample.period }}</div>
        <div class="num big" :class="cls(data.split.out_sample.total_return_pct)">
          {{ pct(data.split.out_sample.total_return_pct) }}
        </div>
        <div class="tiny">回撤 {{ data.split.out_sample.max_drawdown_pct }}% · 交易 {{ data.split.out_sample.trade_count }} 次</div>
      </div>
    </div>

    <div class="tiny" style="margin-top:8px">
      <b>{{ data.split.verdict }}</b>：{{ data.split.advice }}
    </div>

    <div v-for="s in data.sensitivity" :key="s.key" class="sens">
      <div class="between">
        <b class="tiny">参数 {{ s.key }} 敏感性</b>
        <span class="tag" :class="s.verdict?.includes('尖峰') ? 'tag-warn' : ''">{{ s.verdict }}</span>
      </div>
      <table class="table" v-if="s.rows">
        <thead><tr><th>{{ s.key }}</th><th>收益</th><th>回撤</th><th>交易</th></tr></thead>
        <tbody>
          <tr v-for="r in s.rows" :key="r.value" :class="{best: r.value === s.best_value}">
            <td>{{ r.value }}<span v-if="r.value === s.best_value" class="tiny"> 最优</span></td>
            <td class="num" :class="cls(r.total_return_pct)">{{ pct(r.total_return_pct) }}</td>
            <td class="num down">-{{ r.max_drawdown_pct }}%</td>
            <td class="num">{{ r.trade_count }}</td>
          </tr>
        </tbody>
      </table>
      <div class="tiny" style="margin-top:4px">{{ s.advice }}</div>
    </div>

    <div class="tiny" style="margin-top:8px">
      区间 {{ data.period }} · 共 {{ data.bars }} 个交易日
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })

const pct = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const riskClass = computed(() => {
  const r = props.data.risk
  if (r === '高') return 'tag-warn'
  if (r === '低') return 'tag-primary'
  return ''
})
</script>

<style scoped>
.rob { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--bg-soft); }
.split { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
@media (max-width: 560px) { .split { grid-template-columns: 1fr; } }
.half { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.big { font-size: 18px; font-weight: 600; margin: 3px 0; }
.sens { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.best { background: rgba(83,74,183,.06); }
.tag-warn { background: #fdf3e7; color: var(--warn); }
</style>
