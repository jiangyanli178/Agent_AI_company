<template>
  <div class="container" style="padding:20px 16px">
    <div class="between wrap" style="margin-bottom:6px">
      <h2>因子库</h2>
      <span class="tiny">策略的「零件」。看懂了可以自己组合，也可以让 AI 帮你配</span>
    </div>
    <p class="tiny" style="margin-bottom:16px">
      因子就是一个个独立的判断依据，比如"RSI 低于 30"、"价格突破 20 日新高"。把它们拼起来就是一个策略。
    </p>

    <div class="row wrap" style="margin-bottom:16px">
      <input v-model="keyword" class="search" placeholder="搜索因子，比如 RSI、突破、放量" @input="load" />
      <button class="chip" :class="{on: !category}" @click="category='';load()">全部</button>
      <button v-for="c in cats" :key="c.category" class="chip" :class="{on: category===c.category}"
              @click="category=c.category;load()">
        {{ c.category }} {{ c.count }}
      </button>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else class="grid grid-3">
      <div v-for="f in items" :key="f.id" class="card factor" @click="open(f)">
        <div class="between">
          <b>{{ f.name }}</b>
          <span class="tag">{{ f.category }}</span>
        </div>
        <p class="muted" style="margin:8px 0;font-size:13px">{{ f.summary }}</p>
        <div v-if="f.params_hint && f.params_hint !== '—'" class="tiny">参数：{{ f.params_hint }}</div>
      </div>
    </div>
    <div v-if="!loading && !items.length" class="empty">没找到匹配的因子</div>

    <FactorDemo v-model="demoOpen" :factor="current" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import FactorDemo from '../components/FactorDemo.vue'

const items = ref([])
const cats = ref([])
const keyword = ref('')
const category = ref('')
const loading = ref(false)
const demoOpen = ref(false)
const current = ref(null)

let timer = null
async function load() {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    loading.value = true
    try {
      const d = await api.get('/factors', { params: { keyword: keyword.value, category: category.value } })
      items.value = d.items
    } finally { loading.value = false }
  }, 220)
}

function open(f) {
  current.value = f
  demoOpen.value = true
}

onMounted(async () => {
  load()
  try { cats.value = await api.get('/factors/categories') } catch (e) {}
})
</script>

<style scoped>
.search {
  flex: 1; min-width: 200px; padding: 8px 12px;
  border: 1px solid var(--border); border-radius: 8px;
}
.search:focus { outline: none; border-color: var(--primary); }
.chip {
  border:1px solid var(--border); background:#fff; border-radius:18px;
  padding:5px 13px; font-size:13px; color:var(--text-2);
}
.chip.on { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }
.factor { cursor: pointer; transition: border-color .15s; }
.factor:hover { border-color: var(--primary); }
</style>
