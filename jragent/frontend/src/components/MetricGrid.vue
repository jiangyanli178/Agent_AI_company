<template>
  <div class="metrics">
    <!-- 复利口径：赚的钱滚进本金继续投 -->
    <div class="group">
      <div class="group-title">
        复利口径
        <span class="gt-hint">赚的钱滚进本金继续投</span>
      </div>
      <div class="grid-4">
        <div v-for="m in compoundItems" :key="m.key" class="metric" :title="m.hint">
          <div class="tiny label">{{ m.label }}<i class="q" v-if="m.hint">?</i></div>
          <div class="num v" :class="m.color">{{ m.value }}</div>
        </div>
      </div>
    </div>

    <!-- 单利口径：收益直接累加，不回滚本金 -->
    <div class="group">
      <div class="group-title">
        单利口径
        <span class="gt-hint">每笔赚的钱直接加在本金上，不再滚进去</span>
      </div>
      <div class="grid-4">
        <div v-for="m in simpleItems" :key="m.key" class="metric" :title="m.hint">
          <div class="tiny label">{{ m.label }}<i class="q" v-if="m.hint">?</i></div>
          <div class="num v" :class="m.color">{{ m.value }}</div>
        </div>
      </div>
    </div>

    <!-- 与口径无关的通用指标 -->
    <div class="group">
      <div class="group-title">对比与质量</div>
      <div class="grid-4">
        <div v-for="m in commonItems" :key="m.key" class="metric" :title="m.hint">
          <div class="tiny label">{{ m.label }}<i class="q" v-if="m.hint">?</i></div>
          <div class="num v" :class="m.color">{{ m.value }}</div>
        </div>
      </div>
    </div>

    <details class="glossary">
      <summary class="tiny">这些词都是什么意思</summary>
      <ul class="tiny">
        <li><b>复利</b>：赚的钱滚进本金继续投，也就是"利滚利"。本金越来越大，波动也被放大。</li>
        <li><b>单利</b>：每笔赚的钱不滚进本金，直接加在初始资金上算总收益。曲线更平缓。</li>
        <li><b>回撤</b>：从某个高点跌下来亏了多少。最大回撤 20% 就是历史最惨时账户缩水两成。</li>
        <li><b>回撤分母是峰值</b>：比"历史最高点"跌了多少，最常用。</li>
        <li><b>回撤分母是本金</b>：比"一开始投的钱"亏了多少。净值没跌破 1 就是 0。</li>
        <li><b>年化</b>：把总收益换算成"平均每年赚多少"，方便比较不同时长的策略。</li>
        <li><b>夏普</b>：每承担一分波动换回多少收益。算的是每天涨跌的稳定性，<b>和复利单利无关</b>，所以两种口径共用同一个值。1 以上不错，负数说明承担了风险还在亏。</li>
        <li><b>胜率</b>：赚钱的交易占多少。胜率高不等于赚钱，还要看盈亏比和交易次数。</li>
      </ul>
    </details>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ result: { type: Object, required: true } })
const r = computed(() => props.result)

const pct = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(2) + '%')
const dd = (v) => '-' + ((v || 0) * 100).toFixed(1) + '%'
const color = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')

const compoundItems = computed(() => {
  const d = r.value
  return [
    { key: 'ret', label: '复利收益', value: pct(d.total_return), color: color(d.total_return),
      hint: '赚的钱滚进本金继续投，最终的总收益' },
    { key: 'annual', label: '复利年化', value: pct(d.annual_return), color: color(d.annual_return),
      hint: '把复利总收益换算成平均每年赚多少' },
    { key: 'ddPeak', label: '最大回撤（峰值分母）', value: dd(d.max_drawdown), color: 'down',
      hint: '（峰值−当前）÷峰值。从历史最高点跌下来的幅度，行业标准' },
    { key: 'ddPrincipal', label: '最大回撤（本金分母）', value: dd(d.max_drawdown_principal), color: 'down',
      hint: '同一波跌幅 ÷ 本金 1.0。分母比峰值小，所以这个数一定比上面那个大' },
    { key: 'ddLoss', label: '最大亏损（相对本金）', value: dd(d.max_drawdown_loss), color: 'down',
      hint: '最惨时比本金少了多少。只算亏到本金以下的部分，没跌破本金就是 0' },
  ]
})

const simpleItems = computed(() => {
  const d = r.value
  return [
    { key: 'ret', label: '单利收益', value: pct(d.simple_return), color: color(d.simple_return),
      hint: '每笔收益直接累加，不复利' },
    { key: 'annual', label: '单利年化', value: pct(d.simple_annual_return), color: color(d.simple_annual_return),
      hint: '把单利总收益换算成平均每年' },
    // 「单利/复利」是收益怎么算，「峰值/本金」是回撤除以什么 —— 两个独立维度。
    { key: 'ddPeak', label: '最大回撤（峰值分母）',
      value: dd(d.simple_max_drawdown_peak), color: 'down',
      hint: '以单利净值自己的历史高点为分母。和上面「复利·峰值分母」同一套算法，可以直接比' },
    { key: 'ddPrincipal', label: '最大回撤（本金分母）',
      value: dd(d.simple_max_drawdown_principal), color: 'down',
      hint: '同一波跌幅 ÷ 本金 1.0。分母更小，所以一定比峰值分母大' },
    { key: 'ddLoss', label: '最大亏损（相对本金）',
      value: dd(d.simple_max_drawdown_loss), color: 'down',
      hint: '不复利时，最惨比本金少了多少' },
  ]
})

const commonItems = computed(() => {
  const d = r.value
  return [
    { key: 'bench', label: '买入持有', value: pct(d.benchmark_return), color: 'muted',
      hint: '什么都不做，第一天买入一直拿到最后能赚多少' },
    { key: 'excess', label: '超额收益', value: pct(d.excess_return), color: color(d.excess_return),
      hint: '复利收益减去买入持有。负数说明还不如什么都不做' },
    { key: 'sharpe', label: '夏普比率', value: (d.sharpe ?? 0).toFixed(2),
      color: (d.sharpe || 0) > 0 ? '' : 'down',
      hint: '每承担一分波动换回多少收益。它算的是每天涨跌的稳定性，和复利单利无关，所以两种口径共用一个值。1 以上不错，负数说明承担了风险还在亏' },
    { key: 'win', label: '胜率', value: ((d.win_rate ?? 0) * 100).toFixed(1) + '%',
      hint: '赚钱的交易占多少。胜率高不等于赚钱，要看盈亏比和交易次数' },
    { key: 'trades', label: '交易次数', value: String(d.trade_count ?? 0),
      hint: '次数太少（比如 6 次）时，胜率和收益都不可信' },
  ]
})
</script>

<style scoped>
.metrics { margin-top: 16px; }
.group { margin-bottom: 16px; }
.group-title {
  font-size: 12px; color: var(--text); margin-bottom: 8px;
  font-weight: 600; display: flex; align-items: baseline; gap: 8px;
}
.gt-hint { font-weight: 400; color: var(--text-3); font-size: 11px; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
@media (max-width: 700px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
.metric { background: var(--bg-soft); border-radius: 10px; padding: 10px 12px; }
.label { display: flex; align-items: center; gap: 3px; }
.q {
  display: inline-flex; align-items: center; justify-content: center;
  width: 13px; height: 13px; border-radius: 50%;
  border: 1px solid var(--text-3); color: var(--text-3);
  font-size: 9px; font-style: normal; line-height: 1; cursor: help;
}
.v { font-size: 17px; font-weight: 600; margin-top: 3px; }
.glossary { margin-top: 6px; }
.glossary summary { cursor: pointer; color: var(--primary); margin-bottom: 6px; }
.glossary ul { padding-left: 18px; line-height: 1.8; }
.glossary li { margin-bottom: 4px; }
</style>
