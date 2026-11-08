import { defineStore } from 'pinia'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    phone: localStorage.getItem('phone') || '',
    userId: Number(localStorage.getItem('userId') || 0),
    // 管理员标记：只有 ADMIN_PHONE 对应的账号为 true
    isAdmin: localStorage.getItem('isAdmin') === '1',
  }),
  getters: {
    isLogin: (s) => !!s.token,
  },
  actions: {
    setLogin({ token, phone, user_id, is_admin }) {
      this.token = token
      this.phone = phone
      this.userId = user_id
      this.isAdmin = !!is_admin
      localStorage.setItem('token', token)
      localStorage.setItem('phone', phone)
      localStorage.setItem('userId', String(user_id))
      localStorage.setItem('isAdmin', this.isAdmin ? '1' : '0')
    },
    logout() {
      this.token = ''
      this.phone = ''
      this.userId = 0
      this.isAdmin = false
      localStorage.removeItem('token')
      localStorage.removeItem('phone')
      localStorage.removeItem('userId')
      localStorage.removeItem('isAdmin')
    },
  },
})
