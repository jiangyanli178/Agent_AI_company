<template>
  <div class="container" style="padding:20px 16px">
    <div class="card between wrap">
      <div>
        <div class="tiny">当前余额</div>
        <div class="num" style="font-size:32px;font-weight:600">{{ credits.balance }}</div>
        <div class="tiny">累计充值 {{ credits.totalRecharge }} · 累计消耗 {{ credits.totalConsumed }}</div>
      </div>
      <button class="btn" @click="showRecharge()">充值积分</button>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>积分流水</h3>
      <table class="table" style="margin-top:10px">
        <thead><tr><th>类型</th><th>变动</th><th>余额</th><th>说明</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="(t,i) in items" :key="i">
            <td><span class="tag">{{ typeName(t.change_type) }}</span></td>
            <td class="num" :class="t.amount>0?'up':'down'">{{ t.amount>0?'+':'' }}{{ t.amount }}</td>
            <td class="num">{{ t.balance_after }}</td>
            <td class="tiny">{{ t.remark }}</td>
            <td class="tiny">{{ t.created_at.slice(5,16) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!items.length" class="empty">还没有流水记录</div>
      <div class="row" style="justify-content:center;margin-top:12px" v-if="total>size">
        <button class="btn btn-plain btn-sm" :disabled="page<=1" @click="page--;load()">上一页</button>
        <span class="tiny">{{ page }} / {{ Math.ceil(total/size) }}</span>
        <button class="btn btn-plain btn-sm" :disabled="page>=Math.ceil(total/size)" @click="page++;load()">下一页</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import { useCreditsStore } from '../stores/credits'
import { showRecharge } from '../components/rechargeBus'

const credits = useCreditsStore()
const items = ref([])
const page = ref(1)
const size = 20
const total = ref(0)

const typeName = (t) => ({
  RECHARGE: '充值', GIFT: '赠送', CONSUME: '消耗', REFUND: '退回', ADJUST: '调整',
}[t] || t)

async function load() {
  const d = await api.get('/credits/transactions', { params: { page: page.value, size } })
  items.value = d.items
  total.value = d.total
}

onMounted(() => { load(); credits.refresh() })
</script>
