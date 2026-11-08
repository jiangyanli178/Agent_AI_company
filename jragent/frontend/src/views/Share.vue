<template>
  <div class="container" style="padding:20px 16px">
    <div v-if="loading" class="loading">报告加载中…</div>
    <div v-else-if="error" class="empty">{{ error }}</div>
    <div v-else-if="r">
      <div class="notice" style="margin-bottom:14px">
        这是一份由「策略体检所」生成的策略体检报告，可公开查看。
      </div>
      <div class="card">
        <div class="between wrap">
          <h2>{{ r.strategy_name }} × {{ r.symbol_code }}</h2>
          <span class="tag" :class="r.total_return > r.benchmark_return ? 'tag-primary' : ''">
            {{ r.total_return > r.benchmark_return ? '跑赢基准' : '跑输基准' }}
          </span>
        </div>
        <div class="tiny">数据截至 {{ r.data_cutoff }}</div>
        <EquityChart :result="r" :height="340" />
        <MetricGrid :result="r" />
      </div>
      <p class="tiny" style="margin-top:14px">{{ disclaimer }}</p>
      <div class="card" style="margin-top:16px;text-align:center">
        <b>你的策略靠不靠谱？也来测一下</b>
        <p class="tiny" style="margin:8px 0 12px">注册送 100 积分，检索与看策略库永久免费</p>
        <router-link to="/login" class="btn">免费开始</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import EquityChart from '../components/EquityChart.vue'
import MetricGrid from '../components/MetricGrid.vue'

const route = useRoute()
const r = ref(null)
const loading = ref(true)
const error = ref('')
const disclaimer = ref('')

onMounted(async () => {
  try {
    const d = await api.get(`/share/${route.params.token}`)
    r.value = d.result
    disclaimer.value = d.disclaimer
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
})
</script>
