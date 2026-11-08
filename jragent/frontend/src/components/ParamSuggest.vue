<template>
  <div>
    <table class="table">
      <thead><tr><th>参数</th><th>年化</th><th>最大回撤</th><th>交易次数</th></tr></thead>
      <tbody>
        <tr v-for="(s, i) in data.suggestions || []" :key="i">
          <td class="tiny">{{ fmtParams(s.params) }}</td>
          <td class="num" :class="s.annual_return_pct>0?'up':'down'">{{ s.annual_return_pct }}%</td>
          <td class="num down">-{{ Math.abs(s.max_drawdown_pct).toFixed(1) }}%</td>
          <td class="num">{{ s.trade_count }}</td>
        </tr>
      </tbody>
    </table>
    <div class="notice" style="margin-top:8px">{{ data.warning }}</div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
const fmtParams = (p) => Object.entries(p).map(([k, v]) => `${k}=${v}`).join('，')
</script>
