<template>
  <div>
    <NavBar v-if="$route.path !== '/login'" />
    <!--
      Chat 用 keep-alive 缓存：切到策略库再回来，组件不销毁，
      正在流式输出的回答会继续跑完，输入框草稿、滚动位置也都还在。
      之前每次切走都卸载，等于把正在跑的请求连同界面一起扔了。
      其它页面不加缓存，正常重建。
    -->
    <router-view v-slot="{ Component }">
      <keep-alive :include="['Chat']">
        <component :is="Component" />
      </keep-alive>
    </router-view>

    <!-- 页面渲染出错时的兜底：以前是整页白屏、什么提示都没有 -->
    <div v-if="pageError" class="page-error">
      <div class="pe-inner">
        <b>这个页面出错了</b>
        <p class="pe-msg">{{ pageError }}</p>
        <p class="pe-hint">
          点「重试」会重新加载页面；如果刚才更新过代码，按 <b>Ctrl+Shift+R</b> 硬刷新通常就好了。
          一直这样的话，把上面这句发我。
        </p>
        <div class="row" style="margin-top:10px">
          <button class="btn btn-sm" @click="reload">重试</button>
          <button class="btn btn-plain btn-sm" @click="hardReload">硬刷新</button>
          <button class="btn btn-plain btn-sm" @click="pageError = ''">关掉提示</button>
        </div>
      </div>
    </div>

    <RechargeDialog />
    <GuideModal v-model="guideState.visible" />
  </div>
</template>

<script setup>
import { ref, onMounted, onErrorCaptured } from 'vue'
import NavBar from './components/NavBar.vue'
import RechargeDialog from './components/RechargeDialog.vue'
import GuideModal from './components/GuideModal.vue'
import { guideState } from './components/guideBus'
import { useCreditsStore } from './stores/credits'

const credits = useCreditsStore()
onMounted(() => credits.refresh())

// 捕获子组件里的渲染错误，显示成一条可读的提示。
// 典型场景：某个页面用了没导入的东西 → 整个组件 setup 抛 ReferenceError
// → 界面全白，用户完全不知道发生了什么。有了这个至少能看到原因。
const pageError = ref('')

onErrorCaptured((err) => {
  console.error('[页面错误]', err)
  pageError.value = err?.message || String(err)
  return false        // 已处理，不再往上冒泡
})

function reload() {
  pageError.value = ''
  location.reload()
}

// 绕过浏览器缓存重新拉所有资源。
// 改完代码后热更新有时会和 keep-alive 里的旧实例打架，
// 硬刷新是唯一能保证"模板和逻辑是同一版"的办法。
function hardReload() {
  pageError.value = ''
  try {
    location.reload(true)      // 老浏览器认这个参数
  } catch (e) { /* 新浏览器忽略参数，忽略 */ }
  // 用了 Service Worker 的话先注销再重载，否则旧缓存还在
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations()
      .then(rs => Promise.all(rs.map(r => r.unregister())))
      .finally(() => location.reload())
  }
}
</script>

<style scoped>
.page-error {
  position: fixed; inset: 0; z-index: 80;
  background: rgba(250, 250, 247, 0.94);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.pe-inner {
  max-width: 520px; width: 100%; background: #fff;
  border: 1px solid var(--border); border-radius: 12px; padding: 22px;
  box-shadow: 0 10px 30px rgba(30, 30, 25, 0.1);
}
.pe-msg {
  margin: 10px 0 0; font-size: 13px; color: var(--down);
  font-family: ui-monospace, Menlo, Consolas, monospace;
  word-break: break-all; line-height: 1.6;
}
.pe-hint { margin: 8px 0 0; font-size: 12px; color: var(--text-3); }
</style>
