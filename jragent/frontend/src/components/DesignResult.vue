<template>
  <div class="design">
    <div class="between wrap">
      <b>{{ data.strategy }} × {{ data.symbol_code }}（改造版）</b>
      <span class="tag tag-primary" v-if="data.delta.return_pct > 0 || data.delta.drawdown_pct > 0">
        有改善
      </span>
      <span class="tag" v-else>未改善</span>
    </div>

    <div class="filters">
      <div v-for="f in data.filters" :key="f.name" class="filter">
        <b>{{ f.label }}</b>
        <span class="tiny">{{ f.desc }}</span>
        <span class="tiny hit">拦掉 {{ f.blocked_days }} 天（{{ f.blocked_pct }}%）</span>
      </div>
    </div>

    <table class="table" style="margin-top:10px">
      <thead>
        <tr><th>指标</th><th>改造前</th><th>改造后</th><th>变化</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>总收益</td>
          <td class="num" :class="cls(data.base.total_return_pct)">{{ pct(data.base.total_return_pct) }}</td>
          <td class="num" :class="cls(data.designed.total_return_pct)"><b>{{ pct(data.designed.total_return_pct) }}</b></td>
          <td class="num" :class="cls(data.delta.return_pct)">{{ delta(data.delta.return_pct) }}</td>
        </tr>
        <tr>
          <td>最大回撤</td>
          <td class="num down">-{{ data.base.max_drawdown_pct }}%</td>
          <td class="num down">-{{ data.designed.max_drawdown_pct }}%</td>
          <td class="num" :class="data.delta.drawdown_pct > 0 ? 'up' : 'down'">{{ delta(data.delta.drawdown_pct) }}</td>
        </tr>
        <tr>
          <td>夏普</td>
          <td class="num">{{ data.base.sharpe }}</td>
          <td class="num">{{ data.designed.sharpe }}</td>
          <td class="num">{{ (data.designed.sharpe - data.base.sharpe).toFixed(2) }}</td>
        </tr>
        <tr>
          <td>交易次数</td>
          <td class="num">{{ data.base.trade_count }}</td>
          <td class="num">{{ data.designed.trade_count }}</td>
          <td class="num">{{ data.delta.trades }}</td>
        </tr>
        <tr>
          <td>持仓时间</td>
          <td class="num">{{ data.base.exposure_pct }}%</td>
          <td class="num">{{ data.designed.exposure_pct }}%</td>
          <td class="num">{{ delta(data.delta.exposure_pct) }}</td>
        </tr>
        <tr>
          <td>买入持有</td>
          <td class="num muted" colspan="3">{{ pct(data.benchmark_return_pct) }}</td>
        </tr>
      </tbody>
    </table>

    <div class="notice" style="margin-top:10px">{{ data.note }}</div>
    <div class="tiny" style="margin-top:6px">区间 {{ data.period }}</div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })

const pct = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const delta = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(2) + (Math.abs(v) < 1 && v !== 0 ? '' : '%')
</script>

<style scoped>
.design { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--bg-soft); }
.filters { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.filter {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 10px; display: flex; flex-direction: column; gap: 2px;
}
.hit { color: var(--primary); }
</style>
