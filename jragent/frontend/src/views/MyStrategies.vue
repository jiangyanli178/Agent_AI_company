<template>
  <div class="container" style="padding:20px 16px">
    <div class="between wrap" style="margin-bottom:14px">
      <h2>我的策略</h2>
      <span class="tiny">保存下来随时回看，不用重新跑</span>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="!items.length" class="card empty">
      还没有收藏。跑完一次回测后，点结果下面的「保存这个策略」就能存进来。
    </div>

    <div v-else class="grid grid-2">
      <div v-for="s in items" :key="s.id" class="card saved">
        <div class="between wrap">
          <b>{{ s.name }}</b>
          <span class="tag">{{ s.strategy_name }}</span>
        </div>
        <div class="tiny" style="margin:4px 0 10px">
          {{ s.symbol_code }} · {{ fmtParams(s.snapshot?.params) }} · 存于 {{ s.created_at.slice(5,16) }}
        </div>

        <div class="metrics">
          <div>
            <div class="tiny">复利收益</div>
            <div class="num" :class="cls(s.snapshot?.total_return)">{{ pct(s.snapshot?.total_return) }}</div>
          </div>
          <div>
            <div class="tiny">买入持有</div>
            <div class="num muted">{{ pct(s.snapshot?.benchmark_return) }}</div>
          </div>
          <div>
            <div class="tiny">最大回撤</div>
            <div class="num down">-{{ ((s.snapshot?.max_drawdown || 0) * 100).toFixed(1) }}%</div>
          </div>
          <div>
            <div class="tiny">交易次数</div>
            <div class="num">{{ s.snapshot?.trade_count ?? '—' }}</div>
          </div>
        </div>

        <p v-if="s.note" class="tiny note">{{ s.note }}</p>

        <div class="row wrap" style="margin-top:10px">
          <button class="btn btn-plain btn-sm" @click="rerun(s)">用同样参数再跑一次</button>
          <button class="btn btn-plain btn-sm" @click="edit(s)">改名/备注</button>
          <button class="btn btn-plain btn-sm" @click="remove(s)">删除</button>
        </div>
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

const pct = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(2) + '%')
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const fmtParams = (p) => (p ? Object.entries(p).map(([k, v]) => `${k}=${v}`).join('，') : '')

async function load() {
  loading.value = true
  try {
    const d = await api.get('/saved')
    items.value = d.items
  } finally { loading.value = false }
}

function rerun(s) {
  // 带着原参数进策略详情页，用户点一下就能重新跑
  router.push({
    path: `/strategies/${s.strategy_id || s.id}`,
  })
}

async function edit(s) {
  const name = prompt('新名字', s.name)
  if (name === null) return
  const note = prompt('备注（可以留空）', s.note || '')
  if (note === null) return
  try {
    await api.patch(`/saved/${s.id}`, { name, note })
    load()
  } catch (e) { alert(e.message) }
}

async function remove(s) {
  if (!confirm(`确定删除「${s.name}」吗？`)) return
  try {
    await api.delete(`/saved/${s.id}`)
    load()
  } catch (e) { alert(e.message) }
}

onMounted(load)
</script>

<style scoped>
.saved { }
.metrics {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  background: var(--bg-soft); border-radius: 8px; padding: 10px;
}
@media (max-width: 560px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
.metrics .num { font-size: 15px; font-weight: 600; margin-top: 2px; }
.note { margin-top: 8px; color: var(--text-2); }
</style>
