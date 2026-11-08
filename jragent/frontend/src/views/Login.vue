<template>
  <div class="login-wrap">
    <div class="card login-card">
      <h2 style="text-align:center">{{ isRegister ? '注册' : '登录' }}</h2>
      <p class="tiny" style="text-align:center;margin:6px 0 18px">
        {{ isRegister ? '注册即送 100 积分，可立即体验回测与问答' : '欢迎回来' }}
      </p>

      <div class="field">
        <label>手机号</label>
        <input v-model="phone" placeholder="11 位手机号" maxlength="11" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="password" type="password" placeholder="至少 6 位" @keyup.enter="submit" />
      </div>

      <div v-if="error" class="notice" style="color:var(--up);margin-bottom:12px">{{ error }}</div>

      <button class="btn" style="width:100%" :disabled="loading" @click="submit">
        {{ loading ? '请稍候…' : (isRegister ? '注册并领取 100 积分' : '登录') }}
      </button>

      <p class="tiny" style="text-align:center;margin-top:14px">
        <a @click="isRegister=!isRegister;error=''" style="cursor:pointer;color:var(--primary)">
          {{ isRegister ? '已有账号？去登录' : '没有账号？免费注册' }}
        </a>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { useUserStore } from '../stores/user'
import { useCreditsStore } from '../stores/credits'

const route = useRoute()
const router = useRouter()
const user = useUserStore()
const credits = useCreditsStore()
// 默认登录（老用户是大多数）；只有点"注册"进来时才默认注册
const isRegister = ref(route.query.mode === 'register')
const phone = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  if (!/^1[3-9]\d{9}$/.test(phone.value)) { error.value = '手机号格式不正确'; return }
  if (password.value.length < 6) { error.value = '密码至少 6 位'; return }
  loading.value = true
  try {
    const path = isRegister.value ? '/auth/register' : '/auth/login'
    const d = await api.post(path, { phone: phone.value, password: password.value })
    user.setLogin(d)
    credits.balance = d.balance
    // 从被拦截的页面过来的，登录后回到原处
    const redirect = router.currentRoute.value.query.redirect
    router.push(typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
.login-card { width: 380px; max-width: 100%; }
</style>
