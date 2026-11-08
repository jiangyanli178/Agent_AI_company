<template>
  <div class="container" style="padding:22px 0 60px;max-width:900px">
    <div class="between">
      <div>
        <h2 style="margin:0">我的因子</h2>
        <p class="tiny" style="margin:4px 0 0">
          在对话里让 AI 搜出来的<b>过滤条件组合</b>存在这里，随时能翻回来
        </p>
        <p class="tiny" style="margin:6px 0 0;max-width:620px;line-height:1.7">
          这里只存「条件」（比如 ADX 高于 20.5），可以套到任何策略上。
          想连标的和参数一起存、下次直接跑，用结果卡片上的「<b>存进我的策略</b>」——
          那份会进「我的策略」。
        </p>
      </div>
      <router-link to="/" class="btn btn-plain btn-sm">去问 AI</router-link>
    </div>

    <div v-if="loading" class="muted" style="padding:40px 0;text-align:center">读取中…</div>

    <div v-else-if="!items.length" class="empty card" style="margin-top:18px;padding:44px 20px;text-align:center">
      <p>还没有保存过因子组合。</p>
      <p class="tiny">
        去对话里问一句「帮我在茅台上优化 5/20 金叉，把回撤降下来」，
        结果卡片底部有「保存这套因子」。
      </p>
    </div>

    <div v-else style="margin-top:16px">
      <div v-for="f in items" :key="f.id" class="card item">
        <div class="between">
          <div style="min-width:0">
            <b>{{ f.name }}</b>
            <div class="tiny" style="margin-top:3px">
              {{ f.strategy_code || '—' }} · {{ f.symbol_code || '—' }}
              <span v-if="f.created_at" style="margin-left:8px">{{ f.created_at.slice(0, 16) }}</span>
            </div>
          </div>
          <div class="row">
            <button class="link-btn" @click="ask(f)">在对话里接着改</button>
            <button class="link-btn" @click="rename(f)">改名</button>
            <button class="link-btn danger" @click="remove(f)">删除</button>
          </div>
        </div>

        <div class="conds">
          <span v-for="(c, i) in f.conditions" :key="i" class="cond">{{ condLabel(c) }}</span>
        </div>

        <div v-if="f.snapshot && f.snapshot.result" class="row wrap metrics">
          <span>收益 <b>{{ pct(f.snapshot.baseline?.total_return_pct) }} → {{ pct(f.snapshot.result.total_return_pct) }}</b></span>
          <span>回撤 <b>{{ f.snapshot.baseline?.max_drawdown_pct }}% → {{ f.snapshot.result.max_drawdown_pct }}%</b></span>
          <span>交易 <b>{{ f.snapshot.result.trade_count }}</b> 次</span>
          <span v-if="f.snapshot.overfit_risk" class="tag"
                :class="f.snapshot.overfit_risk === '高' ? 'tag-danger' : ''">
            过拟合风险 {{ f.snapshot.overfit_risk }}
          </span>
        </div>

        <p v-if="f.note" class="tiny" style="margin:8px 0 0">{{ f.note }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

const router = useRouter()
const items = ref([])
const loading = ref(true)

const CN = {
  rsi: 'RSI', adx: 'ADX 趋势强度', ma_gap: '价格偏离均线', boll_width: '布林带宽度',
  atr_pct: 'ATR 波动占比', std_pct: '标准差占比', bias: '乖离率', willr: '威廉指标',
  cci: 'CCI', kdj_k: 'KDJ 的 K 值', macd_dif: 'MACD DIF', volume_ratio: '量比',
  obv_slope: 'OBV 变化率', drawdown: '当前回撤',
  price_above_ma: '价格站上均线', ma_slope_up: '均线向上',
}

function condLabel(c) {
  const name = CN[c.factor] || c.factor || '条件'
  if (!c.op) return name
  if (c.factor === 'drawdown') {
    return c.op === 'below' ? `回撤深于 ${Math.abs(c.value)}%` : `回撤浅于 ${Math.abs(c.value)}%`
  }
  const op = { above: '高于', below: '低于', above_eq: '不低于', below_eq: '不高于' }[c.op] || c.op
  return `${name} ${op} ${c.value}`
}

function pct(x) {
  if (x == null) return '—'
  return (Number(x) > 0 ? '+' : '') + Number(x).toFixed(2) + '%'
}

async function load() {
  loading.value = true
  try {
    const d = await api.get('/saved-factors')
    items.value = d.items
  } catch (e) {
    console.warn(e.message)
  } finally {
    loading.value = false
  }
}

function ask(f) {
  const brief = `我想继续优化这套因子组合：\n` +
    `基础策略 ${f.strategy_code}，标的 ${f.symbol_code}，条件：${f.condition_text || ''}\n` +
    `请在此基础上再帮我看还有什么可以改进的方向：\n`
  sessionStorage.setItem('aiPrefill', brief)
  router.push('/')
}

async function rename(f) {
  const name = window.prompt('新的名字', f.name)
  if (!name || name === f.name) return
  try {
    await api.patch(`/saved-factors/${f.id}`, { name })
    await load()
  } catch (e) { alert(e.message) }
}

async function remove(f) {
  if (!window.confirm(`删除「${f.name}」？`)) return
  try {
    await api.delete(`/saved-factors/${f.id}`)
    items.value = items.value.filter(x => x.id !== f.id)
  } catch (e) { alert(e.message) }
}

onMounted(load)
</script>

<style scoped>
.item { margin-bottom: 12px; }
.conds { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.cond {
  font-size: 12px; padding: 3px 9px; border-radius: 999px;
  background: var(--primary-soft, #eeecfa); color: var(--primary);
}
.metrics { gap: 16px; margin-top: 10px; font-size: 12.5px; color: var(--text-2); }
.metrics b { color: var(--text-1); }
.link-btn {
  border: none; background: none; color: var(--text-3);
  font-size: 12px; cursor: pointer;
}
.link-btn:hover { color: var(--primary); }
.link-btn.danger:hover { color: #c0362c; }
.tag-danger { background: #fdf3f2; color: #c0362c; }
</style>
