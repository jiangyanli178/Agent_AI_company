<template>
  <div class="card fs">
    <div class="between">
      <div>
        <b>自动因子搜索</b>
        <span class="tiny" style="margin-left:6px">{{ d.strategy }} · {{ d.symbol_code }}</span>
      </div>
      <span class="tag" :class="riskCls">过拟合风险：{{ riskCn }}</span>
    </div>
    <p class="tiny" style="margin:4px 0 12px">
      目标：{{ d.goal_cn }} · 在 {{ d.bars }} 根 K 线上扫了 {{ d.searched_factors }} 个因子 · {{ d.period }}
    </p>

    <!-- 改前 / 改后 -->
    <table class="cmp">
      <thead>
        <tr><th></th><th>原策略</th><th>加条件后</th><th>变化</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>复利收益</td>
          <td :class="cls(d.baseline.total_return_pct)">{{ pct(d.baseline.total_return_pct) }}</td>
          <td :class="cls(d.result.total_return_pct)"><b>{{ pct(d.result.total_return_pct) }}</b></td>
          <td :class="cls(d.improvement.return_pp)">{{ signed(d.improvement.return_pp) }}pp</td>
        </tr>
        <tr>
          <td>最大回撤</td>
          <td class="down">-{{ d.baseline.max_drawdown_pct }}%</td>
          <td class="down"><b>-{{ d.result.max_drawdown_pct }}%</b></td>
          <td :class="d.improvement.drawdown_pp <= 0 ? 'up' : 'down'">
            {{ signed(d.improvement.drawdown_pp) }}pp
          </td>
        </tr>
        <tr>
          <td>夏普</td>
          <td>{{ d.baseline.sharpe }}</td>
          <td>{{ d.result.sharpe }}</td>
          <td class="muted">—</td>
        </tr>
        <tr>
          <td>胜率</td>
          <td>{{ d.baseline.win_rate_pct }}%</td>
          <td>{{ d.result.win_rate_pct }}%</td>
          <td class="muted">—</td>
        </tr>
        <tr>
          <td>交易次数</td>
          <td>{{ d.baseline.trade_count }}</td>
          <td>{{ d.result.trade_count }}</td>
          <td>{{ signed(d.improvement.trades, 0) }}</td>
        </tr>
        <tr>
          <td>持仓时间占比</td>
          <td>{{ d.baseline.exposure_pct }}%</td>
          <td>{{ d.result.exposure_pct }}%</td>
          <td class="muted">越少越要警惕</td>
        </tr>
      </tbody>
    </table>

    <!-- 为什么选这几个因子 -->
    <div v-if="d.plan && d.plan.candidates && d.plan.candidates.length" class="plan-box">
      <div class="tiny" style="margin-bottom:5px">
        先判断，再验证：这次只试了 {{ d.searched_factors }} 个因子，不是全部穷举
      </div>
      <p v-if="d.plan.reasoning" class="tiny logic">{{ d.plan.reasoning }}</p>
      <div v-for="(c, i) in d.plan.candidates" :key="i" class="cand">
        <span class="cand-name">{{ c.label || c.code }}</span>
        <span class="tiny">{{ c.measures || c.why }}</span>
        <div v-if="c.caveat" class="tiny caveat">注意：{{ c.caveat }}</div>
      </div>
      <div v-if="d.plan.dropped && d.plan.dropped.length" class="tiny dropped">
        排除：<span v-for="(x, i) in d.plan.dropped" :key="i">{{ x.factor }}（{{ x.reason }}）<span v-if="i < d.plan.dropped.length - 1">、</span></span>
      </div>
    </div>

    <!-- 自定义因子：算没算出来要说清楚 -->
    <div v-if="hasCustom" class="custom-box">
      <div v-if="d.custom_factors.accepted.length" class="tiny ok-line">
        ✓ 新增因子已算出来：<span v-for="(c, i) in d.custom_factors.accepted" :key="i">
          {{ c.name }}（{{ c.expression }}）<span v-if="i < d.custom_factors.accepted.length - 1">、</span>
        </span>
      </div>
      <div v-if="d.custom_factors.rejected.length" class="tiny bad-line">
        ✗ 这些公式没算出来：<span v-for="(c, i) in d.custom_factors.rejected" :key="i">
          {{ c.name || c.expression }}（{{ c.reason }}）<span v-if="i < d.custom_factors.rejected.length - 1">；</span>
        </span>
      </div>
    </div>

    <!-- 选中的条件 -->
    <div class="cond-box">
      <div class="tiny" style="margin-bottom:6px">机器选出的条件（按贡献从大到小）</div>
      <div class="row wrap">
        <span v-for="(c, i) in d.conditions" :key="i" class="cond">
          {{ i + 1 }}. {{ condLabel(c) }}
        </span>
      </div>
    </div>

    <!-- 样本外验证 -->
    <div v-if="v && !v.error" class="valid" :class="v.degraded ? 'bad' : 'good'">
      <b>{{ v.degraded ? '⚠ 样本外明显变差' : '✓ 样本外没有明显变差' }}</b>
      <div class="tiny" style="margin-top:3px">
        {{ v.range[0] }} ~ {{ v.range[1] }}（这段没参与搜索）：
        原策略 {{ pct(v.baseline.total_return_pct) }} → 加条件后 {{ pct(v.with_conditions.total_return_pct) }}
      </div>
    </div>

    <!-- 逐步过程 -->
    <details class="steps">
      <summary class="tiny">看搜索过程（{{ d.steps.length }} 步）</summary>
      <div v-for="s in d.steps" :key="s.step" class="step">
        <span class="num">{{ s.step }}</span>
        <div>
          <div>{{ s.label }}</div>
          <div class="tiny">
            拦掉 {{ s.blocked_pct }}% 交易日 →
            收益 {{ pct(s.result.total_return_pct) }}，回撤 {{ s.result.max_drawdown_pct }}%
          </div>
        </div>
      </div>
    </details>

    <p class="warn-line">{{ d.note }}</p>
    <div class="row wrap" style="margin-top:10px;gap:8px">
      <button class="btn btn-sm" @click="saveAsStrategy">存进我的策略</button>
      <button class="btn btn-plain btn-sm" @click="makeReport">让 AI 做成网页报告</button>
      <button class="btn btn-plain btn-sm" @click="save">只存这套因子条件</button>
      <span v-if="saved" class="tiny up">已存</span>
    </div>
    <p class="tiny" style="margin-top:6px">
      区别：<b>存进我的策略</b>会连标的和参数一起存下来，下次直接能跑；
      <b>只存因子条件</b>是一套可以套到任何策略上的过滤规则。
    </p>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

const props = defineProps({ data: { type: Object, required: true } })
const router = useRouter()
const d = computed(() => props.data)
const v = computed(() => d.value.validation)
const saved = ref(false)

// 把这套条件交给 AI 生成网页报告（模块和结论由 AI 按这次的重点定）
// 存进「我的策略」：连标的和参数一起存，下次能直接跑。
// 走对话里的 save_strategy 工具 —— 那里会重跑一遍拿真实指标做快照，
// 比在前端拼一份数据更可靠。
function saveAsStrategy() {
  const x = d.value
  const brief = [
    `把这套策略存进我的策略库：标的 ${x.symbol_code}，基础策略 ${x.strategy}，`,
    `参数 ${JSON.stringify(x.params || {})}，`,
    `过滤条件 ${x.condition_text || '无'}。`,
    `名字就用「${x.symbol_code} ${x.strategy} + ${(x.condition_text || '').split('；')[0]}」。`,
  ].join('')
  sessionStorage.setItem('aiPrefill', brief)
  router.push({ path: '/', query: { prefill: Date.now() } })
}

function makeReport() {
  const x = d.value
  const brief = [
    `把这次因子搜索的结果做成一份网页报告，标的 ${x.symbol_code}，策略 ${x.strategy}。`,
    `入选条件：${x.condition_text}。收益 ${x.result.total_return_pct}%，` +
      `回撤 ${x.result.max_drawdown_pct}%。`,
    `要有 K 线买卖点、复利单利两套净值、回撤图，并把过拟合风险和样本外验证写进结论。`,
  ].join('')
  sessionStorage.setItem('aiPrefill', brief)
  router.push({ path: '/', query: { prefill: Date.now() } })
}

const hasCustom = computed(() => {
  const c = d.value.custom_factors
  return !!(c && ((c.accepted && c.accepted.length) || (c.rejected && c.rejected.length)))
})

const riskCn = computed(() => ({ 低: '低', 中: '中', 高: '高' }[d.value.overfit_risk] || '中'))
const riskCls = computed(() => ({
  低: 'tag-ok', 中: 'tag-warn', 高: 'tag-danger',
}[riskCn.value] || 'tag-warn'))

function pct(x) { return (x > 0 ? '+' : '') + Number(x).toFixed(2) + '%' }
function signed(x, digits = 2) {
  const n = Number(x)
  return (n > 0 ? '+' : '') + n.toFixed(digits)
}
function cls(x) { return Number(x) > 0 ? 'up' : (Number(x) < 0 ? 'down' : '') }

const CN = {
  rsi: 'RSI', adx: 'ADX 趋势强度', ma_gap: '价格偏离均线', boll_width: '布林带宽度',
  atr_pct: 'ATR 波动占比', std_pct: '标准差占比', bias: '乖离率', willr: '威廉指标',
  cci: 'CCI', kdj_k: 'KDJ 的 K 值', macd_dif: 'MACD DIF', volume_ratio: '量比',
  obv_slope: 'OBV 变化率', drawdown: '当前回撤',
  price_above_ma: '价格站上均线', ma_slope_up: '均线向上',
}
function condLabel(c) {
  const name = CN[c.factor] || c.factor
  if (!c.op) return name
  if (c.factor === 'drawdown') {
    return c.op === 'below' ? `回撤深于 ${Math.abs(c.value)}%` : `回撤浅于 ${Math.abs(c.value)}%`
  }
  const op = { above: '高于', below: '低于', above_eq: '不低于', below_eq: '不高于' }[c.op] || c.op
  return `${name} ${op} ${c.value}`
}

async function save() {
  try {
    await api.post('/saved-factors', {
      name: `${d.value.strategy} · ${d.value.symbol_code} 优化版`,
      strategy_code: d.value.strategy_code,
      symbol_code: d.value.symbol_code,
      params: d.value.params,
      conditions: d.value.conditions,
      condition_text: d.value.condition_text,
      snapshot: {
        baseline: d.value.baseline,
        result: d.value.result,
        overfit_risk: d.value.overfit_risk,
      },
    })
    saved.value = true
  } catch (e) {
    alert(e.message)
  }
}
</script>

<style scoped>
.fs { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 8px; }
.cmp { width: 100%; border-collapse: collapse; font-size: 13px; }
.cmp th { text-align: right; font-weight: 500; color: var(--text-3); font-size: 11px; padding: 4px 6px; }
.cmp th:first-child { text-align: left; }
.cmp td { padding: 5px 6px; text-align: right; border-top: 1px solid var(--border-soft); }
.cmp td:first-child { text-align: left; color: var(--text-2); }
.up { color: #c0362c; }
.down { color: #1a7f4b; }
.cond-box { margin-top: 12px; padding: 10px; background: var(--bg-2, #fafaf7); border-radius: 8px; }
.plan-box {
  margin-top: 12px; padding: 10px 11px; border-radius: 8px;
  background: #f7f7fb; border: 1px solid #e6e5f0;
}
.logic { color: var(--text-2); margin: 0 0 8px; line-height: 1.6; }
.cand { margin-bottom: 7px; font-size: 12.5px; }
.cand-name { font-weight: 600; margin-right: 5px; }
.caveat { color: #9a6b00; margin-top: 2px; }
.dropped { color: var(--text-3); margin-top: 4px; }
.custom-box { margin-top: 8px; }
.ok-line { color: #1a7f4b; line-height: 1.6; }
.bad-line { color: #c0362c; line-height: 1.6; margin-top: 3px; }
.cond {
  font-size: 12px; padding: 4px 9px; border-radius: 999px;
  background: #fff; border: 1px solid var(--border); color: var(--text-1);
}
.valid { margin-top: 10px; padding: 9px 11px; border-radius: 8px; font-size: 12.5px; }
.valid.good { background: #f2faf5; border: 1px solid #cfe9d8; color: #1a7f4b; }
.valid.bad { background: #fdf3f2; border: 1px solid #f0d2cf; color: #c0362c; }
.steps { margin-top: 10px; }
.steps summary { cursor: pointer; color: var(--text-3); }
.step { display: flex; gap: 9px; margin-top: 8px; font-size: 12.5px; }
.num {
  width: 18px; height: 18px; flex-shrink: 0; border-radius: 50%;
  background: var(--primary-soft, #eeecfa); color: var(--primary);
  display: flex; align-items: center; justify-content: center; font-size: 11px;
}
.warn-line { font-size: 11.5px; color: var(--text-3); margin: 10px 0 0; line-height: 1.6; }
.tag-ok { background: #f2faf5; color: #1a7f4b; }
.tag-warn { background: #fffbf2; color: #9a6b00; }
.tag-danger { background: #fdf3f2; color: #c0362c; }
</style>
