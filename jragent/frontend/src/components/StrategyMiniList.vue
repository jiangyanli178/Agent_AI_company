<template>
  <div class="mini-list">
    <div v-for="s in items" :key="s.code" class="mini">
      <div class="between">
        <b>{{ s.name }}</b>
        <span class="tag">{{ s.category }}</span>
      </div>
      <div class="tiny">{{ s.summary }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({ items: { type: Array, default: () => [] } })
</script>

<style scoped>
.mini { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 8px; }
</style>
