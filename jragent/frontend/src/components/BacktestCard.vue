<template>
  <div class="bc">
    <div class="between wrap">
      <b>{{ data.strategy }} · {{ data.symbol }}</b>
      <span class="tag" :class="data.beat_benchmark ? 'tag-up' : 'tag-down'">
        {{ data.beat_benchmark ? '跑赢基准' : '跑输基准' }}
      </span>
    </div>

    <!-- 净值曲线：用 SVG 手绘，比再引一遍 ECharts 省 800KB -->
    <svg v-if="hasCurve" class="spark" viewBox="0 0 300 72" preserveAspectRatio="none">
      <line x1="0" y1="71" x2="300" y2="71" stroke="#eeeeee" stroke-width="1"/>
      <polyline :points="benchPoints" fill="none" stroke="#c2c2b8" stroke-width="1.2"
                stroke-dasharray="4 3"/>
      <polyline :points="equityPoints" fill="none" stroke="#534ab7" stroke-width="1.8"/>
    </svg>
    <div v-if="hasCurve" class="tiny legend">
      <span><i class="ln" style="background:#534ab7"></i>本策略</span>
      <span><i class="ln dashed"></i>买入持有</span>
    </div>

    <div class="grid grid-4" style="margin-top:10px">
      <div><div class="tiny">策略收益</div><div class="num big" :class="color(data.total_return_pct)">{{ pct(data.total_return_pct) }}</div></div>
      <div><div class="tiny">买入持有</div><div class="num big muted">{{ pct(data.benchmark_return_pct) }}</div></div>
      <div><div class="tiny">最大回撤</div><div class="num big down">-{{ Math.abs(data.max_drawdown_pct).toFixed(1) }}%</div></div>
      <div><div class="tiny">交易次数</div><div class="num big">{{ data.trade_count }}</div></div>
    </div>
    <div class="tiny" style="margin-top:8px">
      区间 {{ data.range }} · 夏普 {{ data.sharpe }} · 胜率 {{ data.win_rate_pct }}%
    </div>
    <div v-if="data.blocked_orders" class="notice" style="margin-top:8px">
      有 {{ data.blocked_orders }} 次信号因涨跌停无法成交，实盘中这部分收益拿不到
    </div>
    <div class="row wrap" style="margin-top:10px;gap:8px">
      <button class="btn btn-sm" @click="saveStrategy">存进我的策略</button>
      <button class="btn btn-plain btn-sm" @click="makeReport">让 AI 做成网页报告</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({ data: { type: Object, required: true } })
const router = useRouter()

// 存进「我的策略」：走 AI 的 save_strategy 工具，服务端会重跑一遍拿真实指标
function saveStrategy() {
  const d = props.data
  const brief = [
    `把这次回测存进我的策略库：标的 ${d.symbol_code || d.symbol || ''}，`,
    `策略 ${d.strategy}，参数 ${JSON.stringify(d.params || {})}。`,
  ].join('')
  sessionStorage.setItem('aiPrefill', brief)
  router.push({ path: '/', query: { prefill: Date.now() } })
}

// 把这个结果交给 AI 生成一份可打开的网页（标题/模块/结论由 AI 现场定）
function makeReport() {
  const d = props.data
  const brief = [
    `把这次回测做成一份网页报告，标的 ${d.symbol_code || d.symbol || ''}，策略 ${d.strategy}。`,
    `收益 ${d.total_return_pct}%，买入持有 ${d.benchmark_return_pct}%，` +
      `最大回撤 ${d.max_drawdown_pct}%，交易 ${d.trade_count} 次。`,
    `模块你按重点挑，要有 K 线买卖点和回撤图。`,
  ].join('')
  sessionStorage.setItem('aiPrefill', brief)
  router.push({ path: '/', query: { prefill: Date.now() } })
}

const pct = (v) => (v > 0 ? '+' : '') + Number(v || 0).toFixed(1) + '%'
const color = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')

const hasCurve = computed(() =>
  (props.data.equity_sample?.length || 0) > 3 &&
  (props.data.benchmark_sample?.length || 0) > 3)

// 两条线必须共用同一个值域，否则高低关系是假的
const ranges = computed(() => {
  const a = props.data.equity_sample || []
  const b = props.data.benchmark_sample || []
  const all = [...a, ...b].filter(v => typeof v === 'number')
  if (!all.length) return { lo: 0, hi: 1 }
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  if (hi - lo < 1e-6) hi = lo + 0.01
  return { lo, hi }
})

function toPoints(seq) {
  if (!seq?.length) return ''
  const { lo, hi } = ranges.value
  const n = seq.length
  return seq.map((v, i) => {
    const x = (i / (n - 1)) * 300
    const y = 68 - ((v - lo) / (hi - lo)) * 60
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

const equityPoints = computed(() => toPoints(props.data.equity_sample))
const benchPoints = computed(() => toPoints(props.data.benchmark_sample))
</script>

<style scoped>
.bc { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--bg-soft); }
.spark { width: 100%; height: 72px; display: block; margin-top: 10px; }
.legend { display: flex; gap: 14px; margin-top: 4px; }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.ln { display: inline-block; width: 12px; height: 2px; border-radius: 2px; }
.dashed { height: 0; border-top: 2px dashed #c2c2b8; }
.big { font-size: 17px; font-weight: 600; }
.tag-up { background: var(--up-soft); color: var(--up); }
.tag-down { background: var(--down-soft); color: var(--down); }
</style>
