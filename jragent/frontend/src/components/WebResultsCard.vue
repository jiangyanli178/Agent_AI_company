<template>
  <div class="card wr">
    <div class="between">
      <b>联网搜索</b>
      <span class="tag">{{ d.provider }} · {{ d.count }} 条</span>
    </div>
    <p class="tiny" style="margin:4px 0 10px">
      搜索词：{{ d.query }}
    </p>

    <div v-for="(r, i) in d.results" :key="i" class="hit">
      <a :href="r.url" target="_blank" rel="noopener noreferrer" class="hit-title">
        {{ r.title || r.url }}
      </a>
      <div class="hit-url tiny">{{ host(r.url) }}</div>
      <p class="hit-snip">{{ r.snippet }}</p>
    </div>

    <p class="warn-line">{{ d.disclaimer }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ data: { type: Object, required: true } })
const d = computed(() => props.data)

function host(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch (e) {
    return url
  }
}
</script>

<style scoped>
.wr { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 8px; }
.hit { padding: 9px 0; border-top: 1px solid var(--border-soft); }
.hit:first-of-type { border-top: none; }
.hit-title {
  font-size: 13.5px; color: var(--primary); text-decoration: none; font-weight: 500;
  display: block; line-height: 1.5;
}
.hit-title:hover { text-decoration: underline; }
.hit-url { color: var(--text-3); margin-top: 2px; }
.hit-snip {
  font-size: 12.5px; color: var(--text-2); margin: 5px 0 0; line-height: 1.65;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.warn-line {
  font-size: 11.5px; color: #9a6b00; margin: 10px 0 0;
  background: #fffbf2; border: 1px solid #f0e2c2; border-radius: 6px; padding: 7px 9px;
}
</style>
