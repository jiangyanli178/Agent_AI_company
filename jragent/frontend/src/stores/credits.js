import { defineStore } from 'pinia'
import api from '../api'

export const useCreditsStore = defineStore('credits', {
  state: () => ({
    balance: 0,
    totalRecharge: 0,
    totalConsumed: 0,
    loaded: false,
  }),
  actions: {
    async refresh() {
      const user = (await import('./user')).useUserStore()
      if (!user.token) return
      try {
        const d = await api.get('/credits/account')
        this.balance = d.balance
        this.totalRecharge = d.total_recharge
        this.totalConsumed = d.total_consumed
        this.loaded = true
      } catch (e) { /* 余额拉不到不阻塞页面 */ }
    },
  },
})
