<template>
  <table class="table">
    <thead>
      <tr>
        <th>策略</th><th>策略收益</th><th>买入持有</th><th>最大回撤</th><th>胜率</th><th>交易</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(r, i) in rows" :key="i">
        <td>{{ r.strategy }}</td>
        <template v-if="r.error">
          <td colspan="5" class="tiny">{{ r.error }}</td>
        </template>
        <template v-else>
          <td class="num" :class="color(r.total_return_pct)">{{ pct(r.total_return_pct) }}</td>
          <td class="num muted">{{ pct(r.benchmark_return_pct) }}</td>
          <td class="num down">-{{ Math.abs(r.max_drawdown_pct).toFixed(1) }}%</td>
          <td class="num">{{ r.win_rate_pct }}%</td>
          <td class="num">{{ r.trade_count }}</td>
        </template>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
defineProps({ rows: { type: Array, default: () => [] } })
const pct = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%'
const color = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
</script>
