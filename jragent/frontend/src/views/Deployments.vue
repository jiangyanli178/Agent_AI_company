<template>
  <div class="container" style="padding:20px 16px">
    <div class="between wrap" style="margin-bottom:14px">
      <h2>观察列表</h2>
      <span class="tiny">余额 {{ balance }} 积分 · 每策略每天 5 积分</span>
    </div>

    <div v-if="!items.length" class="card empty">
      还没有观察项。去<router-link to="/strategies" style="color:var(--primary)">策略库</router-link>挑一个策略加入，
      或者直接<router-link to="/" style="color:var(--primary)">问 AI</router-link>。
    </div>

    <div v-for="d in items" :key="d.id" class="card" style="margin-bottom:12px">
      <div class="between wrap">
        <div>
          <b>{{ d.strategy_name }}</b> · {{ d.symbol_code }}
          <div class="tiny">每天 {{ d.points_per_day }} 积分 · 约可再跑 {{ d.days_left }} 天</div>
        </div>
        <div class="row">
          <span class="tag" :class="d.status==='RUNNING'?'tag-primary':''">{{ statusName(d.status) }}</span>
          <button class="btn btn-plain btn-sm" @click="act(d, d.status==='RUNNING'?'pause':'resume')">
            {{ d.status==='RUNNING' ? '暂停' : '恢复' }}
          </button>
          <button class="btn btn-plain btn-sm" @click="act(d, 'stop')">移除</button>
        </div>
      </div>

      <div style="margin-top:10px">
        <button class="btn btn-plain btn-sm" @click="loadSignals(d)">
          {{ d._showSignals ? '收起信号' : '查看信号' }}
        </button>
        <table v-if="d._showSignals && d._signals.length" class="table" style="margin-top:10px">
          <thead><tr><th>日期</th><th>动作</th><th>参考价</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="(s,i) in d._signals" :key="i">
              <td class="tiny">{{ s.trade_date }}</td>
              <td><span class="tag" :class="s.action==='BUY'?'tag-up':(s.action==='SELL'?'tag-down':'')">{{ actionName(s.action) }}</span></td>
              <td class="num">{{ s.ref_price }}</td>
              <td class="tiny">{{ s.reason }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="d._showSignals" class="tiny" style="margin-top:8px">
          暂无信号，每日收盘后自动生成
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <b>我的回测记录</b>
      <table class="table" style="margin-top:10px">
        <thead><tr><th>策略</th><th>标的</th><th>时间</th><th></th></tr></thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.job_no">
            <td>{{ j.strategy_name }}</td><td>{{ j.symbol_code }}</td>
            <td class="tiny">{{ j.created_at.slice(5,16) }}</td>
            <td><a @click="$router.push(`/backtest/${j.job_no}`)" style="cursor:pointer;color:var(--primary)">查看</a></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const items = ref([])
const jobs = ref([])
const balance = ref(0)

const statusName = (s) => ({ RUNNING: '运行中', PAUSED: '已暂停', STOPPED: '已停止', EXPIRED: '已到期' }[s] || s)
const actionName = (a) => ({ BUY: '买入', SELL: '卖出', HOLD: '持有' }[a] || a)

async function load() {
  try {
    const d = await api.get('/deployments')
    items.value = d.items
    balance.value = d.balance
  } catch (e) {}
  try {
    const j = await api.get('/backtest/jobs', { params: { size: 20 } })
    jobs.value = j.items
  } catch (e) {}
}

async function act(d, action) {
  try {
    await api.post(`/deployments/${d.id}/${action}`)
    load()
  } catch (e) { alert(e.message) }
}

async function loadSignals(d) {
  d._showSignals = !d._showSignals
  if (d._showSignals && !d._signals) {
    d._signals = await api.get(`/deployments/${d.id}/signals`)
  }
}

onMounted(load)
</script>

<style scoped>
.tag-up { background: var(--up-soft); color: var(--up); }
.tag-down { background: var(--down-soft); color: var(--down); }
</style>
