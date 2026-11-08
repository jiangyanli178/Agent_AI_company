<template>
  <div class="container" style="padding:22px 0 60px;max-width:880px">
    <div class="between">
      <div>
        <h2 style="margin:0">我的报告</h2>
        <p class="tiny" style="margin:4px 0 0">
          AI 在对话里现场生成的网页，随时能回来打开或下载
        </p>
      </div>
      <router-link to="/" class="btn btn-plain btn-sm">去问 AI</router-link>
    </div>

    <div v-if="loading" class="muted" style="padding:40px 0;text-align:center">读取中…</div>

    <div v-else-if="!items.length" class="empty card" style="margin-top:18px;padding:44px 20px;text-align:center">
      <p>还没有生成过报告。</p>
      <p class="tiny">
        在对话里说一句「把这个策略做成网页给我看」，
        AI 会按你的关注点生成一份，链接就存在这里。
      </p>
    </div>

    <div v-else style="margin-top:16px">
      <div v-for="r in items" :key="r.token" class="card item">
        <div class="between">
          <div style="min-width:0">
            <b>{{ r.title }}</b>
            <div class="tiny" style="margin-top:3px">
              {{ r.symbol_code }} · {{ r.strategy }} · {{ (r.size_bytes / 1024).toFixed(1) }} KB
              <span style="margin-left:8px">{{ r.created_at.slice(0, 16) }}</span>
              <span v-if="r.views" style="margin-left:8px">看过 {{ r.views }} 次</span>
            </div>
          </div>
          <div class="row">
            <a :href="r.url" target="_blank" rel="noopener noreferrer" class="link-btn">打开</a>
            <a :href="`/api/reports/${r.token}?download=1`" class="link-btn">下载</a>
            <button class="link-btn danger" @click="remove(r)">删除</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const items = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const d = await api.get('/reports')
    items.value = d.items
  } catch (e) {
    console.warn(e.message)
  } finally {
    loading.value = false
  }
}

async function remove(r) {
  if (!window.confirm(`删除「${r.title}」？链接会立刻失效。`)) return
  try {
    await api.delete(`/reports/${r.token}`)
    items.value = items.value.filter(x => x.token !== r.token)
  } catch (e) { alert(e.message) }
}

onMounted(load)
</script>

<style scoped>
.item { margin-bottom: 10px; }
.link-btn {
  border: none; background: none; color: var(--text-3);
  font-size: 12.5px; cursor: pointer; text-decoration: none;
}
.link-btn:hover { color: var(--primary); }
.link-btn.danger:hover { color: #c0362c; }
</style>
