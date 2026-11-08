<template>
  <div class="card dp">
    <div class="between">
      <b>部署方案 · {{ d.strategy }}</b>
      <span class="tag">包 {{ d.size_kb }} KB</span>
    </div>
    <p class="tiny" style="margin:4px 0 12px">{{ d.template_note }}</p>

    <div class="tiny" style="margin-bottom:6px">买什么服务器</div>
    <div class="plats">
      <div v-for="p in d.platforms" :key="p.name" class="plat">
        <b>{{ p.name }}</b>
        <div class="tiny">{{ p.spec }}</div>
        <div class="price">{{ p.price }}</div>
      </div>
    </div>

    <div class="tiny" style="margin:12px 0 6px">四步部署</div>
    <ol class="steps">
      <li v-for="s in d.steps" :key="s.step">
        <b>{{ s.title }}</b>
        <span class="tiny">{{ s.detail }}</span>
      </li>
    </ol>

    <div class="box">
      <div class="tiny">包里包含 {{ d.files.length }} 个文件</div>
      <div class="row wrap" style="margin-top:5px;gap:6px">
        <span v-for="f in d.files" :key="f.path" class="file">{{ f.path }}</span>
      </div>
    </div>

    <div class="limits">
      <div class="tiny" style="margin-bottom:4px">这个方案不做什么</div>
      <ul>
        <li v-for="(x, i) in d.limits" :key="i">{{ x }}</li>
      </ul>
    </div>

    <router-link to="/deploy" class="btn btn-sm" style="margin-top:10px">
      去部署页下载安装包
    </router-link>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ data: { type: Object, required: true } })
const d = computed(() => props.data)
</script>

<style scoped>
.dp { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 8px; }
.plats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.plat { border: 1px solid var(--border); border-radius: 8px; padding: 9px; background: var(--bg-2, #fafaf7); font-size: 12px; }
.price { color: #c0362c; font-weight: 600; margin-top: 3px; }
.steps { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.75; }
.steps b { margin-right: 4px; }
.steps .tiny { display: block; color: var(--text-3); }
.box { margin-top: 12px; padding: 9px 11px; border-radius: 8px; background: var(--primary-soft, #eeecfa); }
.file { font-size: 11px; color: var(--text-2); background: #fff; padding: 2px 7px; border-radius: 5px; }
.limits { margin-top: 12px; padding: 9px 11px; border-radius: 8px; background: #fffbf2; border: 1px solid #f0e2c2; }
.limits ul { margin: 0; padding-left: 16px; font-size: 12px; line-height: 1.8; color: var(--text-2); }
@media (max-width: 760px) { .plats { grid-template-columns: 1fr; } }
</style>
