<template>
  <div class="container" style="padding:20px 16px">
    <div class="between wrap" style="margin-bottom:16px">
      <h2>策略库</h2>
      <span class="tiny">全部免费查看，跑回测才扣积分</span>
    </div>

    <div class="row wrap" style="margin-bottom:16px">
      <button class="chip" :class="{on: !category}" @click="category='';load()">全部</button>
      <button v-for="c in categories" :key="c.category" class="chip" :class="{on: category===c.category}"
              @click="category=c.category;load()">
        {{ c.category }} {{ c.count }}
      </button>
      <select v-model="difficulty" @change="load" style="margin-left:auto;padding:6px 10px;border:1px solid var(--border);border-radius:8px">
        <option :value="0">全部难度</option>
        <option :value="1">入门</option>
        <option :value="2">进阶</option>
        <option :value="3">高级</option>
      </select>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else class="grid grid-3">
      <div v-for="s in items" :key="s.id" class="card strategy" @click="$router.push(`/strategies/${s.id}`)">
        <div class="between">
          <b>{{ s.name }}</b>
          <span class="tag">{{ s.category }}</span>
        </div>
        <p class="muted" style="margin:8px 0;font-size:13px">{{ s.summary }}</p>
        <div class="between tiny">
          <span>{{ ['','入门','进阶','高级'][s.difficulty] }}</span>
          <span>已回测 {{ s.use_count }} 次</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const items = ref([])
const categories = ref([])
const category = ref('')
const difficulty = ref(0)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.get('/strategies', {
      params: { category: category.value, difficulty: difficulty.value, size: 50 },
    })
    items.value = d.items
  } finally { loading.value = false }
}

onMounted(async () => {
  load()
  try { categories.value = await api.get('/strategies/categories') } catch (e) {}
})
</script>

<style scoped>
.chip { border:1px solid var(--border); background:#fff; border-radius:18px; padding:5px 13px; font-size:13px; color:var(--text-2); }
.chip.on { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }
.strategy { cursor: pointer; transition: border-color .15s; }
.strategy:hover { border-color: var(--primary); }
</style>
