<template>
  <nav class="nav">
    <div class="container between">
      <router-link to="/" class="logo">
        <svg class="mark" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M3 17.5 L8.5 11 L13 14.5 L21 5.5"
                fill="none" stroke="#534ab7" stroke-width="2.4"
                stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="21" cy="5.5" r="2.1" fill="#534ab7"/>
        </svg>
        <span class="logo-text">
          <b>全域策略研究所</b>
          <em>Strategy Lab</em>
        </span>
      </router-link>

      <div class="row links">
        <router-link to="/" class="link">问 AI</router-link>
        <router-link to="/strategies" class="link">策略库</router-link>
        <router-link to="/factors" class="link">因子库</router-link>

        <!-- 下拉：点击也能开，不用靠鼠标悬停去"瞄准" -->
        <div ref="dropRef" class="dropdown" @mouseenter="onEnter" @mouseleave="onLeave">
          <button class="link link-drop" :class="{ open: dropOpen }"
                  @click.stop="toggleDrop">
            我的 <span class="caret">▾</span>
          </button>
          <transition name="drop">
            <div v-show="dropOpen" class="dropdown-menu" @click="closeDrop">
              <router-link to="/saved" class="drop-item">我的策略</router-link>
              <router-link to="/my-factors" class="drop-item">我的因子</router-link>
              <router-link to="/reports" class="drop-item">我的报告</router-link>
              <router-link to="/deployments" class="drop-item">观察列表</router-link>
            </div>
          </transition>
        </div>

        <router-link to="/deploy" class="link">部署上线</router-link>
        <a href="/studio/" class="link">量化工作室</a>
        <router-link v-if="user.isAdmin" to="/admin" class="link link-admin">经营看板</router-link>
      </div>

      <div class="row right">
        <button class="guide-link" @click="openGuide">新手指南</button>
        <router-link v-if="user.isLogin" to="/credits" class="balance" title="积分中心">
          <span class="dot"></span>{{ credits.balance }} 积分
        </router-link>

        <!-- 账号下拉：积分中心 + 退出登录放这里。
             用户习惯是在右上角自己的账号上点，而不是在「我的」里找退出。 -->
        <div v-if="user.isLogin" ref="acctRef" class="dropdown acct">
          <button class="phone-btn" :class="{ open: acctOpen }" @click.stop="toggleAcct">
            {{ maskedPhone }} <span class="caret">▾</span>
          </button>
          <transition name="drop">
            <div v-show="acctOpen" class="dropdown-menu acct-menu" @click="acctOpen = false">
              <router-link to="/credits" class="drop-item">积分中心</router-link>
              <div class="drop-sep"></div>
              <button class="drop-item drop-quit" @click="logout">退出登录</button>
            </div>
          </transition>
        </div>

        <template v-else>
          <router-link to="/login" class="btn btn-plain btn-sm">登录</router-link>
          <router-link to="/login?mode=register" class="btn btn-sm">免费注册</router-link>
        </template>
      </div>
    </div>
  </nav>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'
import { useCreditsStore } from '../stores/credits'
import { openGuide } from './guideBus'

const router = useRouter()
const user = useUserStore()
const credits = useCreditsStore()

// 退出登录：清掉 token 和余额，回到登录页。
// 之前没有这个入口，用户根本退不出去。
function logout() {
  closeDrop()
  acctOpen.value = false
  user.logout()
  credits.balance = 0
  router.push('/login')
}

// 右上角账号下拉：积分中心 + 退出登录
const acctOpen = ref(false)
const acctRef = ref(null)

function toggleAcct() {
  acctOpen.value = !acctOpen.value
  closeDrop()                 // 两个下拉互斥，不同时开
}

// 手机号中间打码，截图/录屏时不会泄露完整号码
const maskedPhone = computed(() => {
  const p = user.phone || ''
  return p.length === 11 ? p.slice(0, 3) + '****' + p.slice(-4) : p
})

// 下拉菜单：点击切换为主，悬停只是顺手的补充。
// 之前纯 hover 判定太刁——鼠标从文字移到菜单要穿过几像素空隙，
// 一不小心就关掉，得试好几次。现在点击必定打开，点击空白才关闭。
const dropOpen = ref(false)
const dropRef = ref(null)
// 点击打开的菜单「钉住」了：鼠标移开也不关，得点空白处才收
// 悬停打开的没钉住：鼠标移开 220ms 后自动收
const pinned = ref(false)
let hoverTimer = null

function toggleDrop() {
  dropOpen.value = !dropOpen.value
  pinned.value = dropOpen.value
}

function closeDrop() {
  dropOpen.value = false
  pinned.value = false
}

function onDocClick(e) {
  if (dropRef.value && !dropRef.value.contains(e.target)) closeDrop()
  if (acctRef.value && !acctRef.value.contains(e.target)) acctOpen.value = false
}

function onEnter() {
  clearTimeout(hoverTimer)
  dropOpen.value = true
}

function onLeave() {
  clearTimeout(hoverTimer)
  if (pinned.value) return          // 钉住的不受鼠标移开影响
  hoverTimer = setTimeout(() => { dropOpen.value = false }, 220)
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  clearTimeout(hoverTimer)
})
</script>

<style scoped>
.nav {
  background: #fff; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 20;
}
.nav .container { height: 58px; gap: 20px; }

.logo { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.mark { flex-shrink: 0; }
.logo-text { display: flex; flex-direction: column; line-height: 1.15; }
.logo-text b { font-size: 15px; font-weight: 600; letter-spacing: 0.2px; white-space: nowrap; }
.logo-text em {
  font-size: 9px; color: var(--text-3); font-style: normal;
  letter-spacing: 1.2px; text-transform: uppercase;
}

.links { gap: 16px; margin-left: 6px; }
.link { color: var(--text-2); font-size: 13.5px; white-space: nowrap; }
.link.router-link-active { color: var(--primary); font-weight: 500; }
.link-admin { color: var(--primary); }
.dropdown { position: relative; display: flex; align-items: center; }
/* 按钮形态，但长得跟导航链接一样 */
.link-drop {
  border: none; background: none; padding: 6px 2px; margin: -6px -2px;
  font-size: 13.5px; color: var(--text-2); cursor: pointer;
  display: inline-flex; align-items: center; gap: 3px;
  font-family: inherit;
}
.link-drop:hover, .link-drop.open { color: var(--primary); }
.caret { font-size: 10px; transition: transform 0.15s; display: inline-block; }
.link-drop.open .caret { transform: rotate(180deg); }

.dropdown-menu {
  position: absolute; top: 100%; left: -8px;
  background: #fff; border: 1px solid var(--border); border-radius: 9px;
  padding: 6px; min-width: 136px; z-index: 45;
  box-shadow: 0 8px 24px rgba(30, 30, 25, 0.12);
}
/* 关键：用伪元素把按钮和菜单之间的空隙填上，
   鼠标滑过去不会"掉"出 hover 区域 */
.dropdown-menu::before {
  content: ""; position: absolute; top: -10px; left: 0; right: 0; height: 10px;
}
.drop-item {
  display: block; padding: 8px 11px; border-radius: 6px;
  font-size: 13px; color: var(--text-2); white-space: nowrap;
}
.drop-item:hover { background: var(--primary-soft, #eeecfa); color: var(--primary); }
.drop-sep { height: 1px; background: var(--border); margin: 5px 4px; }
.drop-quit { width: 100%; text-align: left; background: none; border: none; cursor: pointer; }
.drop-quit:hover { background: #fdf0ef; color: var(--down); }

.drop-enter-active, .drop-leave-active { transition: opacity 0.12s, transform 0.12s; }
.drop-enter-from, .drop-leave-to { opacity: 0; transform: translateY(-4px); }

.right { gap: 12px; margin-left: auto; }
.guide-link {
  font-size: 13px; color: var(--text-2); white-space: nowrap;
  padding: 4px 2px; border-bottom: 1px dashed transparent;
}
.guide-link:hover { color: var(--primary); border-bottom-color: var(--primary); }

.balance {
  display: flex; align-items: center; gap: 5px;
  background: var(--primary-soft); color: var(--primary);
  padding: 4px 11px; border-radius: 20px; font-size: 12px; font-weight: 500;
  white-space: nowrap;
}
.dot { width: 5px; height: 5px; border-radius: 50%; background: var(--primary); }
.phone { white-space: nowrap; }

/* 右上角账号：点一下展开积分中心 / 退出登录 */
.acct { flex-shrink: 0; }
.phone-btn {
  border: 1px solid transparent; background: none; cursor: pointer;
  font-size: 12.5px; color: var(--text-2); padding: 4px 7px; border-radius: 6px;
  display: flex; align-items: center; gap: 3px; white-space: nowrap;
}
.phone-btn:hover, .phone-btn.open { border-color: var(--border); color: var(--primary); }
.acct-menu { right: 0; left: auto; min-width: 118px; }

@media (max-width: 780px) {
  .links { display: none; }
  .logo-text em { display: none; }
  .guide-link { display: none; }
}
</style>
