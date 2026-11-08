<template>
  <div class="container" style="padding:20px 16px" v-if="s">
    <a @click="$router.back()" class="tiny" style="cursor:pointer">← 返回策略库</a>

    <div class="card" style="margin-top:12px">
      <div class="between wrap">
        <h2>{{ title }}</h2>
        <span class="tag tag-primary">{{ s.category }}</span>
      </div>
      <p class="explain">{{ explainText }}</p>
      <details class="more">
        <summary class="tiny">这个策略的详细说明</summary>
        <p class="muted" style="margin-top:8px;font-size:13px">{{ s.detail }}</p>
      </details>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="between wrap">
        <h3>跑一次真实回测</h3>
        <span class="tag tag-primary">免费</span>
      </div>
      <p class="tiny" style="margin:4px 0 14px">
        数据截至 {{ cutoff }} · 回测免费，只有让 AI 深度解读才需要积分
      </p>

      <div class="grid grid-2">
        <div class="field">
          <label>选择股票</label>
          <select v-model="symbol">
            <option v-for="x in symbols" :key="x.code" :value="x.code">{{ x.name }}（{{ x.code }}）</option>
          </select>
        </div>
        <div class="field">
          <label>用多长时间的数据来验证</label>
          <select v-model="years">
            <option :value="1">近 1 年</option>
            <option :value="3">近 3 年</option>
            <option :value="5">近 5 年</option>
          </select>
        </div>
      </div>

      <div class="grid grid-3">
        <div class="field" v-for="(spec, key) in s.param_schema" :key="key">
          <label>{{ paramLabel(key, spec) }}</label>
          <!-- 开关型参数（比如「只在零轴上方买」）不能画成数字输入框 -->
          <label v-if="spec.type === 'bool'" class="switch">
            <input type="checkbox" v-model="params[key]" />
            <span>{{ params[key] ? '已开启' : '关闭' }}</span>
          </label>
          <input v-else type="number" v-model.number="params[key]"
                 :min="spec.min" :max="spec.max" :step="spec.type==='float'?0.01:1" />
          <div class="hint">{{ paramHint(key, spec) }}</div>
        </div>
      </div>

      <!-- 策略自带过滤条件时要说清楚，否则用户不知道回测里还叠了一层 -->
      <div v-if="(s.filters || []).length" class="filters-tip">
        这个策略自带过滤条件：<b>{{ filtersText }}</b>
        <span class="tiny">（回测时会自动叠加，不是裸策略的结果）</span>
      </div>

      <div class="row wrap">
        <button class="btn" :disabled="loading" @click="run">
          {{ loading ? '正在用历史数据试跑…' : '开始回测' }}
        </button>
        <span class="tiny">参数改了，上面标题会跟着变</span>
      </div>
      <div v-if="error" class="notice" style="color:var(--up);margin-top:10px">{{ error }}</div>
    </div>

    <div v-if="result" style="margin-top:16px">
      <div class="card">
        <div class="between wrap">
          <h3>{{ resultTitle }}</h3>
          <span class="tag" :class="result.total_return > result.benchmark_return ? 'tag-primary' : ''">
            {{ result.total_return > result.benchmark_return ? '跑赢基准' : '跑输基准' }}
          </span>
        </div>

        <!-- 原股票K线 + 买卖点，最能帮新手理解的部分 -->
        <section class="sect">
          <h4>这段历史里，它在哪天买、哪天卖</h4>
          <p class="tiny">红色三角是买入，绿色三角是卖出。滚轮放大能看清每一次交叉。</p>
          <CandleChart :result="result" :height="420" />
        </section>

        <section class="sect">
          <h4>复利：赚的钱继续投进去</h4>
          <p class="tiny">紫色是本策略，灰色虚线是一直拿着不动。下面红色区域是回撤（从高点跌了多少）。</p>
          <ReturnChart :result="result" mode="compound" :height="300" />
        </section>

        <section class="sect">
          <h4>单利：赚的钱不滚进去，直接加在本金上</h4>
          <p class="tiny">同样的交易，但因为不复利，结果会和上面不一样。回撤按初始本金算。</p>
          <ReturnChart :result="result" mode="simple" :height="300" />
        </section>

        <MetricGrid :result="result" />

        <div v-if="result.blocked_orders" class="notice" style="margin-top:12px">
          有 {{ result.blocked_orders }} 次想买却买不进（涨停了），实盘里这部分收益是拿不到的。
        </div>

        <div class="row wrap" style="margin-top:14px">
          <button class="btn" @click="askAI">让 AI 帮我优化这个策略</button>
          <button class="btn btn-ghost" @click="saveThis">保存这个策略</button>
          <button class="btn btn-plain btn-sm" @click="exportPy">导出 Python 脚本</button>
          <button class="btn btn-plain btn-sm" @click="deployPack">下载部署包</button>
          <button class="btn btn-plain btn-sm" @click="exportDataZip">下载回测明细</button>
          <a class="btn btn-plain btn-sm"
             :href="`/api/symbols/${symbol}/export?years=${years}`"
             :download="`${symbol}_日线.csv`">下载日线数据</a>
          <button class="btn btn-plain btn-sm" @click="share">生成分享链接</button>
          <span v-if="shareLink" class="tiny">{{ shareLink }}</span>
        </div>
        <p class="tiny" style="margin:8px 0 0">
          部署包里有 Docker 配置和每日任务，传到自己的服务器上就能每天自动出信号 ·
          <router-link to="/deploy" class="up">看部署步骤</router-link>
        </p>
        <div v-if="savedTip" class="notice" style="margin-top:10px">{{ savedTip }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api, { syncBalance } from '../api'
import ReturnChart from '../components/ReturnChart.vue'
import CandleChart from '../components/CandleChart.vue'
import MetricGrid from '../components/MetricGrid.vue'
import { paramLabel, paramHint, displayName, strategyExplain } from '../utils/params'

const route = useRoute()
const router = useRouter()
const s = ref(null)
const symbols = ref([])
const symbol = ref('600519')
const years = ref(3)
const params = ref({})
const loading = ref(false)
const result = ref(null)
const error = ref('')
const cutoff = ref('')
const jobNo = ref('')
const shareLink = ref('')
const savedTip = ref('')
const freeLeft = ref(0)

// 标题跟随真实参数，而不是一直显示策略库默认名
const title = computed(() => s.value ? displayName(s.value, params.value) : '')
const explainText = computed(() => s.value ? strategyExplain(s.value, params.value) : '')

// 策略自带过滤条件时，把「因子 高于 阈值」翻译成人话
const filtersText = computed(() => {
  const list = s.value?.filters || []
  return list.map(f => {
    const opMap = { above: '高于', below: '低于', is_true: '成立', is_false: '不成立' }
    return `${f.factor} ${opMap[f.op] || f.op}${f.value != null ? ' ' + f.value : ''}`
  }).join('，')
})
const resultTitle = computed(() =>
  result.value ? `${title.value} × ${result.value.symbol_code}` : '')

onMounted(async () => {
  s.value = await api.get(`/strategies/${route.params.id}`)
  params.value = { ...s.value.default_params }
  symbols.value = await api.get('/symbols')
  const meta = await api.get('/symbols/meta/cutoff')
  cutoff.value = meta.latest_trade_date
  await refreshFree()
})

async function refreshFree() {
  try {
    const d = await api.get('/backtest/jobs', { params: { size: 50 } })
    const today = new Date().toISOString().slice(0, 10)
    const used = (d.items || []).filter(j => j.created_at?.slice(0, 10) === today).length
    freeLeft.value = Math.max(0, 2 - used)
  } catch (e) {}
}

async function run() {
  loading.value = true; error.value = ''; shareLink.value = ''
  try {
    const d = await api.post('/backtest', {
      strategy_id: s.value.id, symbol_code: symbol.value,
      years: years.value, params: params.value,
    })
    result.value = d.result
    jobNo.value = d.job_no
    freeLeft.value = d.free_left ?? freeLeft.value
    syncBalance(d.balance)
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function share() {
  try {
    const d = await api.post(`/backtest/jobs/${jobNo.value}/share`)
    shareLink.value = location.origin + d.path
  } catch (e) { error.value = e.message }
}

// 带着完整的回测上下文跳到对话页，用户只需补一句"我想怎么优化"
function askAI() {
  const r = result.value
  if (!r) return
  const pct = (v) => (v > 0 ? '+' : '') + (v * 100).toFixed(2) + '%'
  const sym = symbols.value.find(x => x.code === symbol.value)
  const brief = [
    `我刚跑了「${title.value}」在 ${sym ? sym.name : symbol.value}（${symbol.value}）上近 ${years.value} 年的回测：`,
    `策略收益 ${pct(r.total_return)}，买入持有 ${pct(r.benchmark_return)}，`,
    `最大回撤 ${(r.max_drawdown * 100).toFixed(1)}%，胜率 ${(r.win_rate * 100).toFixed(1)}%，成交 ${r.trade_count} 次。`,
    ``,
    `请帮我分析这个结果，并给出优化方向。我希望往这个方向优化：`,
  ].join('\n')

  sessionStorage.setItem('aiPrefill', brief)
  router.push('/')
}

// 下载可独立运行的 .py，用户可以在自己电脑/服务器上跑
function exportPy() {
  if (!jobNo.value) return
  const token = localStorage.getItem('token') || ''
  const url = `/api/backtest/jobs/${jobNo.value}/export`
  fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    .then(r => {
      if (!r.ok) throw new Error(`下载失败（${r.status}）`)
      return r.blob()
    })
    .then(blob => {
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${symbol.value}_${jobNo.value}.py`
      a.click()
      URL.revokeObjectURL(a.href)
      savedTip.value = '已下载，按脚本里的说明 pip install akshare pandas 就能跑'
      setTimeout(() => { savedTip.value = '' }, 6000)
    })
    .catch(e => { savedTip.value = e.message })
}

// 部署包：策略代码 + Docker + 每日任务，用户传到自己的服务器上跑
async function deployPack() {
  if (!jobNo.value) return
  const token = localStorage.getItem('token') || ''
  try {
    const r = await fetch('/api/deploy/package', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ job_no: jobNo.value }),
    })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      throw new Error(j.msg || `下载失败（${r.status}）`)
    }
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `jragent-${symbol.value}.zip`
    a.click()
    URL.revokeObjectURL(a.href)
    savedTip.value = '部署包已下载。解压后看 README.md，里面写了买服务器和一条命令部署的步骤'
    setTimeout(() => { savedTip.value = '' }, 8000)
  } catch (e) {
    savedTip.value = e.message
  }
}

// 回测明细：净值曲线 + 逐笔交易 + 口径说明，打包下载
async function exportDataZip() {
  if (!jobNo.value) return
  const token = localStorage.getItem('token') || ''
  try {
    const r = await fetch(`/api/backtest/jobs/${jobNo.value}/export-zip`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      throw new Error(j.msg || `下载失败（${r.status}）`)
    }
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `回测明细_${symbol.value}.zip`
    a.click()
    URL.revokeObjectURL(a.href)
    savedTip.value = '已下载。解压后有三个文件：净值曲线.csv、逐笔交易.csv、说明.txt'
    setTimeout(() => { savedTip.value = '' }, 8000)
  } catch (e) {
    savedTip.value = e.message
  }
}

// 收藏这次回测，以后能在「我的策略」里直接看
async function saveThis() {
  if (!jobNo.value) return
  const defaultName = `${title.value} · ${symbol.value}`
  const name = prompt('给它起个名字，方便以后找到', defaultName)
  if (name === null) return
  try {
    const d = await api.post('/saved', { job_no: jobNo.value, name })
    savedTip.value = `已保存到「我的策略」：${d.name}`
    setTimeout(() => { savedTip.value = '' }, 4000)
  } catch (e) {
    savedTip.value = `保存失败：${e.message}`
  }
}
</script>

<style scoped>
.explain { margin: 10px 0; font-size: 14px; line-height: 1.7; }
.more { margin-top: 6px; }
.more summary { cursor: pointer; color: var(--primary); }
.hint { font-size: 11px; color: var(--text-3); margin-top: 4px; line-height: 1.5; }
/* 开关型参数：和数字输入框等高，看起来是同一排控件 */
.switch {
  display: flex; align-items: center; gap: 8px;
  height: 38px; padding: 0 10px; cursor: pointer;
  border: 1px solid var(--border); border-radius: 8px; background: #fff;
  font-size: 13px;
}
.switch input { width: 15px; height: 15px; accent-color: var(--primary); cursor: pointer; }
.switch span { color: var(--text-2); }
.filters-tip {
  margin-top: 12px; padding: 9px 12px; border-radius: 8px;
  background: var(--primary-soft, #eeecfa); color: var(--text);
  font-size: 12.5px; line-height: 1.6;
}
.sect { margin-top: 20px; }
.sect h4 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.sect .tiny { margin-bottom: 8px; }
</style>
