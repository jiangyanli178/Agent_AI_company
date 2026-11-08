<template>
  <div v-if="visible" class="mask" @click.self="close">
    <div class="panel">
      <div class="between" style="margin-bottom:12px">
        <b>插入策略或因子</b>
        <button class="tiny" @click="close">关闭</button>
      </div>
      <p class="tiny" style="margin-bottom:12px">
        选中后会自动填进输入框，你可以接着说"帮我组合这两个"或者"在这个基础上加个过滤条件"。
      </p>

      <div class="row wrap" style="margin-bottom:12px;gap:6px">
        <button class="tab" :class="{on: tab==='strategy'}" @click="switchTab('strategy')">策略库</button>
        <button class="tab" :class="{on: tab==='factor'}" @click="switchTab('factor')">因子库</button>
        <!-- 自己存过的东西就在这儿，不用再翻出去找 -->
        <button class="tab" :class="{on: tab==='myStrategy'}" @click="switchTab('myStrategy')">我的策略</button>
        <button class="tab" :class="{on: tab==='myFactor'}" @click="switchTab('myFactor')">我的因子</button>
        <input v-model="keyword" class="search" placeholder="搜索…" @input="load" />
      </div>

      <div class="list">
        <div v-if="loading" class="tiny" style="padding:20px;text-align:center">加载中…</div>
        <div v-for="it in items" :key="it.key" class="item" :class="{on: picked.includes(it.key)}" @click="toggle(it)">
          <div class="between">
            <span>{{ it.label }}</span>
            <span class="tag" v-if="it.tag">{{ it.tag }}</span>
          </div>
          <div class="tiny">{{ it.desc }}</div>
        </div>
        <div v-if="!loading && !items.length" class="tiny" style="padding:20px;text-align:center">
          没有匹配的内容
        </div>
      </div>

      <div class="between" style="margin-top:14px">
        <span class="tiny">已选 {{ picked.length }} 项</span>
        <div class="row">
          <button class="btn btn-plain btn-sm" @click="picked=[]">清空</button>
          <button class="btn btn-sm" :disabled="!picked.length" @click="confirm">插入到输入框</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import api from '../api'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'pick'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const tab = ref('strategy')
const keyword = ref('')
const items = ref([])
const picked = ref([])
const loading = ref(false)
let timer = null

async function load() {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    loading.value = true
    try {
      const kw = keyword.value || ''
      if (tab.value === 'strategy') {
        const d = await api.get('/strategies', { params: { keyword: kw, size: 30 } })
        items.value = d.items.map(s => ({
          key: `S:${s.id}`, label: s.name, tag: s.category,
          desc: s.summary, insert: `【策略：${s.name}】`,
        }))
      } else if (tab.value === 'factor') {
        const d = await api.get('/factors', { params: { keyword: kw, limit: 40 } })
        items.value = d.items.map(f => ({
          key: `F:${f.id}`, label: f.name, tag: f.category,
          desc: f.summary, insert: `【因子：${f.name}】`,
        }))
      } else if (tab.value === 'myStrategy') {
        const d = await api.get('/saved')
        const list = (d.items || []).filter(s => !kw || s.name.includes(kw))
        items.value = list.map(s => {
          const sn = s.snapshot || {}
          return {
            key: `MS:${s.id}`,
            label: s.name,
            tag: '我的策略',
            desc: `${s.symbol_code} · 收益 ${(sn.total_return * 100).toFixed(2)}% · `
                  + `回撤 ${(Math.abs(sn.max_drawdown || 0) * 100).toFixed(2)}%`
                  + (sn.filters_desc ? ` · ${sn.filters_desc}` : ''),
            // 把我的策略的完整配置交给 AI，它才复现得出来
            insert: `【我的策略：${s.name}｜标的 ${s.symbol_code}`
                    + `｜参数 ${JSON.stringify(sn.params || {})}`
                    + (sn.filters_desc ? `｜过滤 ${sn.filters_desc}` : '') + '】',
          }
        })
      } else {
        const d = await api.get('/saved-factors')
        const list = (d.items || []).filter(s => !kw || s.name.includes(kw))
        items.value = list.map(s => ({
          key: `MF:${s.id}`,
          label: s.name,
          tag: '我的因子',
          desc: `${s.symbol_code} · ${s.condition_text || '无过滤条件'}`,
          insert: `【我的因子：${s.name}｜标的 ${s.symbol_code}`
                  + `｜条件 ${s.condition_text || ''}`
                  + `｜参数 ${JSON.stringify(s.params || {})}】`,
        }))
      }
    } catch (e) {
      items.value = []
    } finally { loading.value = false }
  }, 220)
}

function switchTab(t) {
  tab.value = t
  picked.value = []
  load()
}

function toggle(it) {
  const i = picked.value.indexOf(it.key)
  if (i >= 0) picked.value.splice(i, 1)
  else picked.value.push(it.key)
}

function confirm() {
  const chosen = items.value.filter(x => picked.value.includes(x.key))
  emit('pick', chosen)
  picked.value = []
  close()
}

function close() { visible.value = false }

watch(() => props.modelValue, (v) => { if (v) { keyword.value = ''; load() } })
</script>

<style scoped>
.mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.42);
  display: flex; align-items: center; justify-content: center; z-index: 200; padding: 16px;
}
.panel {
  background: #fff; border-radius: 14px; padding: 20px;
  width: 560px; max-width: 100%; max-height: 86vh; display: flex; flex-direction: column;
}
.tab {
  padding: 6px 14px; border-radius: 8px; font-size: 13px;
  border: 1px solid var(--border); background: #fff; color: var(--text-2);
}
.tab.on { background: var(--primary-soft); border-color: var(--primary); color: var(--primary); }
.search {
  flex: 1; margin-left: 8px; padding: 7px 11px;
  border: 1px solid var(--border); border-radius: 8px;
}
.search:focus { outline: none; border-color: var(--primary); }
.list { flex: 1; overflow-y: auto; margin-top: 4px; min-height: 200px; }
.item {
  border: 1px solid var(--border); border-radius: 8px;
  padding: 9px 11px; margin-bottom: 8px; cursor: pointer;
}
.item:hover { border-color: var(--primary); }
.item.on { border-color: var(--primary); background: var(--primary-soft); }
</style>
