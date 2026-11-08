<template>
  <div class="card rp">
    <div class="between">
      <div style="min-width:0">
        <b class="rp-title">{{ d.title }}</b>
        <div class="tiny" style="margin-top:3px">
          {{ d.symbol_code }} · {{ d.size_kb }} KB · {{ sectionText }}
        </div>
      </div>
      <span class="tag">AI 生成</span>
    </div>

    <div class="row wrap" style="margin-top:10px;gap:8px">
      <!-- 必须用 api_url（/api/reports/xxx，服务端直接吐 HTML），
           用 url（/reports/xxx）会落到前端路由，进到「我的报告」列表而不是这份报告 -->
      <a :href="openUrl" target="_blank" rel="noopener noreferrer" class="btn btn-sm">
        打开报告
      </a>
      <a :href="d.download_url" class="btn btn-plain btn-sm">下载网页文件</a>
      <a :href="d.script_url" class="btn btn-plain btn-sm">下载策略脚本</a>
      <button class="btn btn-plain btn-sm" @click="showPreview = !showPreview">
        {{ showPreview ? '收起预览' : '在这里预览' }}
      </button>
    </div>

    <p class="tiny" style="margin:8px 0 0">{{ d.hint }}</p>

    <div v-if="showPreview" class="preview">
      <iframe :src="d.api_url" loading="lazy" title="报告预览"></iframe>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ data: { type: Object, required: true } })
const d = computed(() => props.data)
const showPreview = ref(false)

// 服务端返回的是 /api/reports/{token}；旧数据里只有 url（前端路由），做一次兼容
const openUrl = computed(() => d.value.api_url || `/api/reports/${d.value.token}`)

const NAMES = {
  kline: '买卖点', equity: '复利', simple: '单利', drawdown: '回撤',
  metrics: '指标', trades: '逐笔交易', hysteresis: '回滞对比',
}
const sectionText = computed(
  () => (d.value.sections || []).map(s => NAMES[s] || s).join(' · ')
)
</script>

<style scoped>
.rp { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 8px; }
.rp-title { font-size: 14px; display: block; }
.preview {
  margin-top: 10px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  background: #fafaf7;
}
.preview iframe { width: 100%; height: 520px; border: none; display: block; }
</style>
