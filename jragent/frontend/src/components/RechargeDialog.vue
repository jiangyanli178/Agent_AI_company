<template>
  <div v-if="rechargeState.visible" class="mask" @click.self="close">
    <div class="dialog">
      <div class="between" style="margin-bottom:14px">
        <h3>充值积分</h3>
        <button class="tiny" @click="close">关闭</button>
      </div>

      <div v-if="rechargeState.need" class="notice" style="margin-bottom:12px">
        本次操作需要 {{ rechargeState.need }} 积分，当前余额 {{ rechargeState.have }} 积分
      </div>

      <!-- 第一步：选套餐 -->
      <div v-if="!order">
        <div v-if="packsLoading" class="tiny" style="padding:14px 0">正在读取套餐…</div>
        <div v-else-if="packsError" class="notice" style="background:#fffbf2;border-color:#f0d9a8">
          {{ packsError }}
          <button class="link-btn" style="margin-left:8px" @click="loadPacks">重试</button>
        </div>
        <div v-else class="grid grid-2">
          <div v-for="p in packs" :key="p.id" class="pack" :class="{active: selected===p.id}" @click="selected=p.id">
            <div class="between">
              <b>{{ p.name }}</b>
              <span v-if="p.tag" class="tag tag-primary">{{ p.tag }}</span>
            </div>
            <div class="price num">¥{{ p.price_cny }}</div>
            <div class="tiny">{{ p.total_points }} 积分 · 约 {{ p.unit_price }} 元/分</div>
          </div>
        </div>
        <button class="btn" style="width:100%;margin-top:16px"
                :disabled="!selected||loading||packsLoading" @click="createOrder">
          {{ loading ? '创建订单中…' : (selected ? '去支付' : '请先选一个套餐') }}
        </button>
        <p class="tiny" style="margin-top:10px">积分不可提现、不可转让、不可兑换现金。</p>
      </div>

      <!-- 第二步：扫码 + 核销 -->
      <div v-else>
        <div class="notice">
          请支付 <b class="num">¥{{ order.price_cny }}</b>，备注订单号后 6 位：<b>{{ order.order_no.slice(-6) }}</b>
        </div>
        <div class="qr">
          <img :src="order.qr_image" alt="收款码" @error="qrError=true" v-if="!qrError" />
          <div v-else class="tiny">收款码图片未配置，请直接联系站长转账并告知订单号</div>
        </div>
        <div class="field">
          <label>付款后填写备注后 6 位</label>
          <input v-model="suffix" placeholder="例如 ABC123" maxlength="12" />
        </div>
        <button class="btn" style="width:100%" :disabled="loading||!suffix" @click="confirm">
          {{ loading ? '核销中…' : '我已完成支付' }}
        </button>
        <p class="tiny" style="margin-top:8px">订单号：{{ order.order_no }}</p>
      </div>

      <div v-if="done" class="success">
        到账成功，当前余额 {{ doneBalance }} 积分
        <button class="btn" style="width:100%;margin-top:12px" @click="close">继续使用</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import { rechargeState, hideRecharge } from './rechargeBus'
import { useCreditsStore } from '../stores/credits'

const credits = useCreditsStore()
const packs = ref([])
const selected = ref(null)
const packsLoading = ref(true)
const packsError = ref('')
const order = ref(null)
const suffix = ref('')
const loading = ref(false)
const qrError = ref(false)
const done = ref(false)
const doneBalance = ref(0)

// 套餐必须能看见才叫能充钱。之前接口挂了就静默留空，
// 用户只看到一个禁用的「去支付」，完全不知道发生了什么。
async function loadPacks() {
  packsLoading.value = true
  packsError.value = ''
  try {
    const list = await api.get('/credits/packs')
    packs.value = list || []
    if (!packs.value.length) {
      packsError.value = '还没有配置充值套餐，请联系站长'
    } else if (!selected.value) {
      selected.value = packs.value[0].id      // 默认选第一个，少点一次
    }
  } catch (e) {
    packsError.value = `套餐读取失败：${e.message}`
  } finally {
    packsLoading.value = false
  }
}

onMounted(loadPacks)

function close() {
  hideRecharge()
  order.value = null; suffix.value = ''; done.value = false
}

async function createOrder() {
  loading.value = true
  try { order.value = await api.post('/credits/recharge', { pack_id: selected.value }) }
  catch (e) { alert(e.message) }
  finally { loading.value = false }
}

async function confirm() {
  loading.value = true
  try {
    const d = await api.post('/credits/confirm', { order_no: order.value.order_no, payer_suffix: suffix.value })
    doneBalance.value = d.balance
    credits.balance = d.balance
    done.value = true
  } catch (e) { alert(e.message) }
  finally { loading.value = false }
}
</script>

<style scoped>
.mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.4);
  display: flex; align-items: center; justify-content: center; z-index: 100; padding: 16px;
}
.dialog {
  background: #fff; border-radius: 14px; padding: 22px;
  width: 460px; max-width: 100%; max-height: 88vh; overflow-y: auto;
}
.pack { border: 1px solid var(--border); border-radius: 10px; padding: 12px; cursor: pointer; }
.pack.active { border-color: var(--primary); background: var(--primary-soft); }
.price { font-size: 20px; font-weight: 600; margin: 6px 0 2px; }
.qr { text-align: center; padding: 14px 0; }
.qr img { max-width: 220px; width: 100%; border: 1px solid var(--border); border-radius: 8px; }
.success { margin-top: 16px; text-align: center; color: var(--down); }
</style>
