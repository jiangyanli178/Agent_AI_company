import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

// requiresAuth：没登录不让进（会跳登录页并记住原地址）
// requiresAdmin：只有 ADMIN_PHONE 对应账号能进
const routes = [
  { path: '/', name: 'chat', component: () => import('../views/Chat.vue'), meta: { title: '策略体检所' } },
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { title: '登录' } },
  { path: '/strategies', name: 'strategies', component: () => import('../views/StrategyList.vue'), meta: { title: '策略库' } },
  { path: '/factors', name: 'factors', component: () => import('../views/Factors.vue'), meta: { title: '因子库' } },
  { path: '/saved', name: 'saved', component: () => import('../views/MyStrategies.vue'), meta: { title: '我的策略', requiresAuth: true } },
  { path: '/my-factors', name: 'my-factors', component: () => import('../views/MyFactors.vue'), meta: { title: '我的因子', requiresAuth: true } },
  { path: '/reports', name: 'reports', component: () => import('../views/MyReports.vue'), meta: { title: '我的报告', requiresAuth: true } },
  { path: '/deploy', name: 'deploy', component: () => import('../views/Deploy.vue'), meta: { title: '部署上线' } },
  { path: '/strategies/:id', name: 'strategy-detail', component: () => import('../views/StrategyDetail.vue'), meta: { title: '策略详情' } },
  { path: '/backtest/:jobNo', name: 'backtest', component: () => import('../views/BacktestResult.vue'), meta: { title: '回测报告' } },
  { path: '/credits', name: 'credits', component: () => import('../views/Credits.vue'), meta: { title: '积分中心', requiresAuth: true } },
  { path: '/deployments', name: 'deployments', component: () => import('../views/Deployments.vue'), meta: { title: '观察列表', requiresAuth: true } },
  { path: '/s/:token', name: 'share', component: () => import('../views/Share.vue'), meta: { title: '策略体检报告' } },
  {
    path: '/admin',
    name: 'admin',
    component: () => import('../views/Admin.vue'),
    meta: { title: '经营看板', requiresAuth: true, requiresAdmin: true },
  },
  // 兜底：路径写错不要白屏
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const user = useUserStore()

  if (to.meta.requiresAuth && !user.isLogin) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // 管理员页面：不是管理员就直接回首页。
  // 后端接口本来就会 403，但页面不该让普通用户看到空壳 ——
  // 那会让人以为"系统坏了"，也会暴露后台有哪些模块。
  if (to.meta.requiresAdmin && !user.isAdmin) {
    return { path: '/', query: { denied: 'admin' } }
  }

  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 策略体检所` : '策略体检所'
})

export default router
