<template>
  <div class="container" style="padding:20px 16px">
    <div v-if="loading" class="loading">报告加载中…</div>
    <div v-else-if="error" class="empty">{{ error }}</div>
    <div v-else-if="r">
      <div class="card">
        <div class="between wrap">
          <div>
            <h2>{{ r.strategy_name }} × {{ r.symbol_code }}</h2>
            <div class="tiny">区间 {{ r.dates[0] }} ~ {{ r.dates[r.dates.length-1] }} · 数据截至 {{ r.data_cutoff }}</div>
          </div>
          <span class="tag" :class="r.total_return > r.benchmark_return ? 'tag-primary' : ''">
            {{ r.total_return > r.benchmark_return ? '跑赢基准' : '跑输基准' }}
          </span>
        </div>
        <EquityChart :result="r" :height="360" />
        <MetricGrid :result="r" />
      </div>

      <div class="card" style="margin-top:14px" v-if="r.blocked_orders">
        <b>流动性风险提示</b>
        <p class="muted" style="margin-top:6px">
          本策略在历史上有 {{ r.blocked_orders }} 次交易信号因触及涨跌停而无法成交。
          实盘中这部分收益是拿不到的，请以此为准评估策略。
        </p>
      </div>

      <div class="card" style="margin-top:14px">
        <b>当前信号</b>
        <p class="muted" style="margin-top:6px">{{ r.latest_signal_explain }}</p>
      </div>

      <div class="card" style="margin-top:14px" v-if="r.trades?.length">
        <b>交易明细（最近 {{ Math.min(20, r.trades.length) }} 笔）</b>
        <table class="table" style="margin-top:10px">
          <thead><tr><th>买入日</th><th>卖出日</th><th>持有天数</th><th>盈亏</th></tr></thead>
          <tbody>
            <tr v-for="(t,i) in r.trades.slice(-20).reverse()" :key="i">
              <td>{{ t.entry_date }}</td><td>{{ t.exit_date }}</td>
              <td class="num">{{ t.bars_held }}</td>
              <td class="num" :class="t.pnl_pct>0?'up':'down'">{{ (t.pnl_pct*100).toFixed(2) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="tiny" style="margin-top:14px">
        历史表现不代表未来，回测收益通常高于实盘。本报告为技术工具输出，不构成投资建议。
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import EquityChart from '../components/EquityChart.vue'
import MetricGrid from '../components/MetricGrid.vue'

const route = useRoute()
const r = ref(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const d = await api.get(`/backtest/jobs/${route.params.jobNo}`)
    r.value = d.result_json
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
})
</script>
