import axios from 'axios'
import { useUserStore } from '../stores/user'
import { useCreditsStore } from '../stores/credits'
import { showRecharge } from '../components/rechargeBus'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,   // AI 工具循环可能要跑几十秒，别设太短
})

// 请求：自动带 token
api.interceptors.request.use((config) => {
  const user = useUserStore()
  if (user.token) config.headers.Authorization = `Bearer ${user.token}`
  return config
})

// 把 axios 的英文报错和 HTTP 状态码统一翻成人话
function toChineseError(err, data) {
  // 后端返回的中文提示优先用，它最具体
  if (data?.msg) return data.msg

  const status = err.response?.status
  if (status) {
    const map = {
      400: '请求参数有误，请检查输入',
      401: '请先登录',
      403: '没有权限执行这个操作',
      404: '请求的内容不存在',
      405: '操作方式不支持',
      409: '数据冲突，请刷新后重试',
      422: '提交的信息格式不正确',
      429: '操作太频繁了，请稍后再试',
      500: '服务器出错了，请稍后再试',
      502: '服务暂时不可用，请稍后再试',
      503: '服务维护中，请稍后再试',
      504: '服务响应超时，请稍后再试',
    }
    if (map[status]) return map[status]
    if (status >= 500) return '服务器出错了，请稍后再试'
    return `请求失败（${status}）`
  }

  // 网络层错误：后端没起来、断网、跨域被拦
  if (err.code === 'ECONNABORTED') return '请求超时，请检查网络后重试'
  if (err.message === 'Network Error') return '连不上服务器，请确认后端已启动（端口 8000）'
  return err.message || '操作失败，请稍后再试'
}

// 响应：拆包 + 统一错误处理
api.interceptors.response.use(
  (res) => {
    const body = res.data
    if (body && body.code === 0) return body.data
    return Promise.reject(new Error(body?.msg || '请求失败'))
  },
  (err) => {
    const status = err.response?.status
    const data = err.response?.data

    // 积分不足：弹充值框，不让用户面对一串报错
    if (status === 402) {
      showRecharge(data?.data || {})
      return Promise.reject(new Error(data?.msg || '积分不足，请先充值'))
    }
    if (status === 401) {
      const user = useUserStore()
      user.logout()
      if (location.pathname !== '/login') location.href = '/login'
      return Promise.reject(new Error('登录已过期，请重新登录'))
    }
    return Promise.reject(new Error(toChineseError(err, data)))
  },
)

// 每次对话/回测后同步余额到顶栏
export function syncBalance(balance) {
  if (typeof balance === 'number') {
    const credits = useCreditsStore()
    credits.balance = balance
  }
}

// 流式接口用 fetch，拿不到 axios 拦截器，需要手动带 token
export function getUserToken() {
  return localStorage.getItem('token') || ''
}

export default api
