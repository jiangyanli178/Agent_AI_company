<template>
  <div class="container" style="padding:20px 16px">
    <h2 style="margin-bottom:14px">经营看板</h2>

    <div v-if="forbidden" class="card" style="padding:30px;text-align:center">
      <b>没有权限</b>
      <p class="tiny" style="margin-top:6px">
        经营看板只对管理员账号开放。当前账号（{{ user.phone }}）不是管理员。
      </p>
      <router-link to="/" class="btn btn-sm" style="margin-top:10px">回到对话</router-link>
    </div>

    <div v-else-if="!d" class="loading">加载中…</div>
    <template v-else>
      <div class="grid grid-4">
        <div class="card"><div class="tiny">累计 GMV</div><div class="num big">¥{{ d.gmv_cny }}</div></div>
        <div class="card"><div class="tiny">付费用户</div><div class="num big">{{ d.paying_users }} / {{ d.total_users }}</div></div>
        <div class="card"><div class="tiny">token 成本</div><div class="num big">¥{{ d.token_cost_cny }}</div></div>
        <div class="card"><div class="tiny">毛利率</div><div class="num big">{{ d.gross_margin_pct ?? '—' }}%</div></div>
      </div>

      <div class="card" style="margin-top:14px">
        <div class="between">
          <b>账目自查</b>
          <span class="tag" :class="d.ledger.consistent ? 'tag-primary' : ''">
            {{ d.ledger.consistent ? '一致' : '异常' }}
          </span>
        </div>
        <p class="tiny" style="margin-top:6px">
          账户余额合计 {{ d.ledger.balance_sum }} 积分，流水合计 {{ d.ledger.tx_sum }} 积分。
          预收沉淀 {{ d.outstanding_points }} 积分（约 ¥{{ d.outstanding_cny }}，属递延非收入）。
        </p>
      </div>

      <div class="card" style="margin-top:14px">
        <div class="between">
          <b>待确认收款</b>
          <span class="tag" :class="orders.pending_count ? 'tag-warn' : 'tag-primary'">
            {{ orders.pending_count }} 笔待核销
          </span>
        </div>
        <p class="tiny" style="margin:4px 0 10px">
          用户扫码付款后会变成「待核销」。你确认钱到账了再点核销，积分才会入账。
        </p>
        <table class="table">
          <thead><tr><th>订单号</th><th>用户</th><th>金额</th><th>积分</th><th>状态</th><th>时间</th><th></th></tr></thead>
          <tbody>
            <tr v-for="o in orders.items" :key="o.order_no">
              <td class="tiny">{{ o.order_no.slice(-8) }}</td>
              <td class="tiny">{{ o.phone }}</td>
              <td class="num">¥{{ o.price_cny }}</td>
              <td class="num">{{ o.points }}</td>
              <td><span class="tag" :class="o.status==='PAID'?'tag-primary':'tag-warn'">
                {{ o.status === 'PAID' ? '已入账' : '待核销' }}</span></td>
              <td class="tiny">{{ o.created_at.slice(5,16) }}</td>
              <td>
                <button v-if="o.status==='PENDING'" class="btn btn-sm" @click="verify(o)">确认收款</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!orders.items.length" class="tiny" style="margin-top:8px">还没有订单</div>
      </div>

      <div class="card" style="margin-top:14px">
        <b>积分流水</b>
        <div class="row wrap" style="gap:10px;margin:10px 0">
          <span v-for="s in ledger.summary" :key="s.type" class="tag">
            {{ typeName(s.type) }} {{ s.count }} 笔 / {{ s.total > 0 ? '+' : '' }}{{ s.total }}
          </span>
        </div>
        <table class="table">
          <thead><tr><th>类型</th><th>用户</th><th>变动</th><th>余额</th><th>说明</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="t in ledger.items.slice(0, 25)" :key="t.id">
              <td class="tiny">{{ typeName(t.change_type) }}</td>
              <td class="tiny">{{ t.phone }}</td>
              <td class="num" :class="t.amount>0?'up':'down'">{{ t.amount>0?'+':'' }}{{ t.amount }}</td>
              <td class="num">{{ t.balance_after }}</td>
              <td class="tiny">{{ t.remark }}</td>
              <td class="tiny">{{ t.created_at.slice(5,16) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card" style="margin-top:14px">
        <b>AI 员工绩效</b>
        <table class="table" style="margin-top:10px">
          <thead><tr><th>Agent</th><th>运行次数</th><th>成功率</th><th>平均耗时</th><th>成本</th></tr></thead>
          <tbody>
            <tr v-for="k in kpi" :key="k.agent_code">
              <td>{{ k.agent_code }}</td>
              <td class="num">{{ k.total_runs }}</td>
              <td class="num">{{ k.success_rate }}%</td>
              <td class="num">{{ k.avg_latency_ms }}ms</td>
              <td class="num">¥{{ k.total_cost_cny }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!kpi.length" class="tiny" style="margin-top:8px">暂无运行记录</div>
      </div>

      <div class="card" style="margin-top:14px">
        <b>异常与事故</b>
        <div v-for="(i, idx) in incidents" :key="idx" class="incident">
          <div class="between">
            <span class="tag">{{ i.status }}</span>
            <span class="tiny">{{ i.created_at }}</span>
          </div>
          <div class="tiny">{{ i.agent_code }} / {{ i.task_type }} · 重试 {{ i.retry_count }} 次</div>
          <div class="tiny">{{ i.output_digest || i.error_msg }}</div>
        </div>
        <div v-if="!incidents.length" class="tiny" style="margin-top:8px">暂无异常记录</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import { useUserStore } from '../stores/user'
const forbidden = ref(false)
const user = useUserStore()
const d = ref(null)
const kpi = ref([])
const incidents = ref([])
const orders = ref({ pending_count: 0, items: [] })
const ledger = ref({ summary: [], items: [] })

const typeName = (t) => ({
  RECHARGE: '充值', GIFT: '赠送', CONSUME: '消耗', REFUND: '退回', ADJUST: '调整',
}[t] || t)

async function loadOrders() {
  try { orders.value = await api.get('/admin/orders') } catch (e) {}
}

async function verify(o) {
  if (!confirm(`确认已收到 ¥${o.price_cny}？确认后 ${o.points} 积分会立即入账。`)) return
  try {
    await api.post(`/admin/orders/${o.order_no}/verify`)
    await loadOrders()
    d.value = await api.get('/admin/dashboard')
  } catch (e) { alert(e.message) }
}

onMounted(async () => {
  try {
    d.value = await api.get('/admin/dashboard')
    kpi.value = await api.get('/admin/agent-kpi')
    incidents.value = await api.get('/admin/incidents')
    await loadOrders()
    ledger.value = await api.get('/admin/ledger')
  } catch (e) {
    // 前端守卫已经挡过一次，这里是兜底：
    // 万一 localStorage 被改过（isAdmin 伪造成 true），后端仍会 403，界面给明确提示
    forbidden.value = true
    console.warn(e.message)
  }
})
</script>

<style scoped>
.big { font-size: 24px; font-weight: 600; margin-top: 4px; }
.incident { border-top: 1px solid var(--border); padding: 10px 0; }
.incident:first-of-type { border-top: none; }
</style>
